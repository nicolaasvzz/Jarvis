using System.Globalization;
using System.Net.Http.Json;
using System.Text.Json;

namespace TradeBot;

public class AlpacaOrderException(string message) : Exception(message);

public record AlpacaFill(string Symbol, decimal Qty, decimal Price);

// Thin wrapper over Alpaca's trading + crypto market data REST APIs.
// Hard-refuses anything but the paper endpoint -- this bot doesn't yet
// have a deliberate path to real money, and shouldn't grow one by accident.
public class AlpacaClient
{
    public const string PaperBaseUrl = "https://paper-api.alpaca.markets";
    private const string DataBaseUrl = "https://data.alpaca.markets";

    private readonly HttpClient _http;

    public AlpacaClient(AlpacaOptions options)
    {
        if (string.IsNullOrWhiteSpace(options.ApiKey) || string.IsNullOrWhiteSpace(options.SecretKey))
        {
            throw new InvalidOperationException(
                "Alpaca:ApiKey / Alpaca:SecretKey are not set. Run:\n" +
                "  dotnet user-secrets set \"Alpaca:ApiKey\" \"your key id\"\n" +
                "  dotnet user-secrets set \"Alpaca:SecretKey\" \"your secret\"");
        }
        if (!string.Equals(options.BaseUrl.TrimEnd('/'), PaperBaseUrl, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                $"Refusing to run: Alpaca:BaseUrl is '{options.BaseUrl}', not the paper endpoint " +
                $"('{PaperBaseUrl}'). This bot only ever trades paper for now.");
        }

        _http = new HttpClient();
        _http.DefaultRequestHeaders.Add("APCA-API-KEY-ID", options.ApiKey);
        _http.DefaultRequestHeaders.Add("APCA-API-SECRET-KEY", options.SecretKey);
    }

    public async Task<JsonElement> GetAccountAsync()
    {
        var resp = await _http.GetAsync($"{PaperBaseUrl}/v2/account");
        resp.EnsureSuccessStatusCode();
        return await resp.Content.ReadFromJsonAsync<JsonElement>();
    }

    public async Task<decimal> GetLatestTradePriceAsync(string symbol)
    {
        var url = $"{DataBaseUrl}/v1beta3/crypto/us/latest/trades?symbols={Uri.EscapeDataString(symbol)}";
        var resp = await _http.GetAsync(url);
        resp.EnsureSuccessStatusCode();
        var json = await resp.Content.ReadFromJsonAsync<JsonElement>();
        return json.GetProperty("trades").GetProperty(symbol).GetProperty("p").GetDecimal();
    }

    // Submits a market order, polls briefly for the fill, and returns it.
    // Throws AlpacaOrderException if the order is rejected or never fills.
    public async Task<AlpacaFill> SubmitMarketOrderAsync(string symbol, decimal qty, string side)
    {
        var payload = new
        {
            symbol,
            qty = qty.ToString(CultureInfo.InvariantCulture),
            side,
            type = "market",
            time_in_force = "gtc", // crypto has no session to close a "day" order against
        };
        var resp = await _http.PostAsJsonAsync($"{PaperBaseUrl}/v2/orders", payload);
        if (!resp.IsSuccessStatusCode)
        {
            var body = await resp.Content.ReadAsStringAsync();
            throw new AlpacaOrderException(
                $"Alpaca rejected order {JsonSerializer.Serialize(payload)}: {resp.StatusCode} {body}");
        }

        var order = await resp.Content.ReadFromJsonAsync<JsonElement>();
        var orderId = order.GetProperty("id").GetString()!;

        for (var attempt = 0; attempt < 15; attempt++)
        {
            await Task.Delay(1000);
            var statusResp = await _http.GetAsync($"{PaperBaseUrl}/v2/orders/{orderId}");
            statusResp.EnsureSuccessStatusCode();
            var status = await statusResp.Content.ReadFromJsonAsync<JsonElement>();
            var isFilled = status.GetProperty("status").GetString() == "filled";
            var hasPrice = status.TryGetProperty("filled_avg_price", out var priceEl)
                           && priceEl.ValueKind == JsonValueKind.String;
            if (isFilled && hasPrice)
            {
                return new AlpacaFill(symbol, qty, decimal.Parse(priceEl.GetString()!, CultureInfo.InvariantCulture));
            }
        }

        throw new AlpacaOrderException(
            $"Order {orderId} for {qty} {symbol} did not report a fill in time -- check the Alpaca dashboard.");
    }
}
