namespace TradeBot;

// Step 1 of the redo: prove a real, long-running process stays alive and
// ticks on its own -- no strategy, no broker call yet. Order placement gets
// wired into this same loop once this is confirmed running.
public class Worker(ILogger<Worker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        logger.LogInformation("TradeBot starting up.");
        var tick = 0;

        while (!stoppingToken.IsCancellationRequested)
        {
            tick++;
            logger.LogInformation("TradeBot heartbeat #{Tick} at {Time}", tick, DateTimeOffset.Now);
            await Task.Delay(TimeSpan.FromSeconds(2), stoppingToken);
        }

        logger.LogInformation("TradeBot shutting down after {Tick} heartbeats.", tick);
    }
}
