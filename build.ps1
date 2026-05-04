param(
    [Parameter(Position=0)]
    [string]$Command = "help"
)

function Show-Help {
    Write-Host "url-summarizer-temporal Commands:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  dev          Start Docker infrastructure in background" -ForegroundColor Green
    Write-Host "  infra        Same as dev" -ForegroundColor Green
    Write-Host "  infra-stop   Stop Docker infrastructure" -ForegroundColor Green
    Write-Host "  infra-wipe   Stop infrastructure and delete all data volumes" -ForegroundColor Green
    Write-Host "  agent        Run the agent worker + ACP server (infra must be running)" -ForegroundColor Green
    Write-Host "  ui           Start the Next.js UI dev server" -ForegroundColor Green
    Write-Host ""
    Write-Host "Usage: .\build.ps1 <command>" -ForegroundColor Yellow
}

function Invoke-Infra {
    Write-Host "Starting Docker infrastructure..." -ForegroundColor Cyan
    docker compose up --build -d
    if ($LASTEXITCODE -ne 0) { exit 1 }
    Write-Host "Infrastructure started. Run '.\build.ps1 agent' in a new terminal." -ForegroundColor Green
    Write-Host "Run '.\build.ps1 ui' in another terminal for the UI." -ForegroundColor Green
}

function Invoke-InfraStop {
    Write-Host "Stopping Docker infrastructure..." -ForegroundColor Cyan
    docker compose down
}

function Invoke-InfraWipe {
    Write-Host "Stopping infrastructure and wiping data..." -ForegroundColor Cyan
    docker compose down -v
}

function Invoke-Agent {
    Write-Host "Starting agent worker + ACP server..." -ForegroundColor Cyan
    $env:PYTHONUTF8 = "1"
    .\.venv\Scripts\agentex agents run --manifest manifest.yaml
}

function Invoke-UI {
    Write-Host "Starting UI dev server..." -ForegroundColor Cyan
    Push-Location agentex-ui
    npm run dev
    Pop-Location
}

switch ($Command.ToLower()) {
    "help"        { Show-Help }
    "dev"         { Invoke-Infra }
    "infra"       { Invoke-Infra }
    "infra-stop"  { Invoke-InfraStop }
    "infra-wipe"  { Invoke-InfraWipe }
    "agent"       { Invoke-Agent }
    "ui"          { Invoke-UI }
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Show-Help
        exit 1
    }
}

