using Microsoft.Extensions.Configuration;
using TradeBot;

if (args.Contains("smoke-test"))
{
    var configuration = new ConfigurationBuilder()
        .AddJsonFile("appsettings.json", optional: true)
        .AddUserSecrets<AlpacaOptions>(optional: true)
        .AddEnvironmentVariables()
        .Build();
    var alpacaOptions = configuration.GetSection(AlpacaOptions.SectionName).Get<AlpacaOptions>() ?? new AlpacaOptions();
    return await SmokeTest.RunAsync(alpacaOptions, args);
}

var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddHostedService<Worker>();

var host = builder.Build();
host.Run();
return 0;
