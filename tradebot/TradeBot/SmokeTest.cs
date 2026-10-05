using System.Globalization;

namespace TradeBot;

// Standalone connectivity check: fire one BTC/USD paper trade and close it.
// No strategy, no Worker loop -- just proves the Alpaca wiring works.
// Run with: dotnet run -- smoke-test [--symbol BTC/USD] [--notional 10] [--qty 0.001] [--yes] [--dry-run]
public static class SmokeTest
{
    public static async Task<int> RunAsync(AlpacaOptions options, string[] args)
    {
        var symbol = GetOption(args, "--symbol") ?? "BTC/USD";
        var notional = decimal.Parse(GetOption(args, "--notional") ?? "10", CultureInfo.InvariantCulture);
        var qtyArg = GetOption(args, "--qty");
        var skipConfirm = HasFlag(args, "--yes");
        var dryRun = HasFlag(args, "--dry-run");

        if (dryRun)
        {
            Console.WriteLine(
                $"[dry-run] would BUY {qtyArg ?? $"~{notional} notional worth of"} {symbol} " +
                $"on {AlpacaClient.PaperBaseUrl}, then SELL the same qty to close.");
            return 0;
        }

        AlpacaClient client;
        try
        {
            client = new AlpacaClient(options);
        }
        catch (InvalidOperationException ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 1;
        }

        var account = await client.GetAccountAsync();
        Console.WriteLine(
            $"Connected to paper account. equity=${account.GetProperty("equity").GetString()} " +
            $"buying_power=${account.GetProperty("buying_power").GetString()}");

        var price = await client.GetLatestTradePriceAsync(symbol);
        var qty = qtyArg is not null
            ? decimal.Parse(qtyArg, CultureInfo.InvariantCulture)
            : Math.Round(notional / price, 8);
        Console.WriteLine(qtyArg is not null
            ? $"Latest {symbol} price ~${price:N2}"
            : $"Latest {symbol} price ~${price:N2} -> buying {qty} ({notional} notional)");

        if (qty <= 0)
        {
            Console.Error.WriteLine("Computed/given qty is not positive, aborting.");
            return 1;
        }

        if (!skipConfirm)
        {
            Console.Write($"About to BUY {qty} {symbol} on the PAPER endpoint, then close it. Continue? [y/N] ");
            var reply = Console.ReadLine();
            if (!string.Equals(reply?.Trim(), "y", StringComparison.OrdinalIgnoreCase))
            {
                Console.WriteLine("Aborted.");
                return 1;
            }
        }

        Console.WriteLine($"Submitting BUY {qty} {symbol}...");
        AlpacaFill buyFill;
        try
        {
            buyFill = await client.SubmitMarketOrderAsync(symbol, qty, "buy");
        }
        catch (AlpacaOrderException ex)
        {
            Console.Error.WriteLine($"Buy failed: {ex.Message}. No position opened.");
            return 1;
        }
        Console.WriteLine($"BUY filled: qty={buyFill.Qty} price=${buyFill.Price:N2}");

        Console.WriteLine($"Submitting SELL {buyFill.Qty} {symbol} to close...");
        try
        {
            var closeFill = await client.SubmitMarketOrderAsync(symbol, buyFill.Qty, "sell");
            Console.WriteLine($"SELL filled: qty={closeFill.Qty} price=${closeFill.Price:N2}");
            var pnl = (closeFill.Price - buyFill.Price) * buyFill.Qty;
            Console.WriteLine($"\nRound trip complete. Gross P&L (paper, before fees): ${pnl:N4}");
            Console.WriteLine("PASS: buy + close both filled on Alpaca's crypto paper endpoint.");
            return 0;
        }
        catch (AlpacaOrderException ex)
        {
            Console.Error.WriteLine(
                $"\n!!! CLOSE FAILED: {ex.Message}\n" +
                $"You have an OPEN position: {buyFill.Qty} {symbol}. Close it manually in the " +
                $"Alpaca dashboard, or re-run: dotnet run -- smoke-test --qty {buyFill.Qty} --yes\n");
            return 1;
        }
    }

    private static bool HasFlag(string[] args, string flag) => args.Contains(flag);

    private static string? GetOption(string[] args, string name)
    {
        var index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }
}
