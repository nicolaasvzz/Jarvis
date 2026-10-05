namespace TradeBot;

public class AlpacaOptions
{
    public const string SectionName = "Alpaca";

    public string ApiKey { get; set; } = "";
    public string SecretKey { get; set; } = "";
    public string BaseUrl { get; set; } = AlpacaClient.PaperBaseUrl;
}
