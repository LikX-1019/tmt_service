$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
$sqlFile = Join-Path $PSScriptRoot "sql\init_qa_database.sql"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing .env file: $envFile"
}
if (-not (Test-Path -LiteralPath $sqlFile)) {
    throw "Missing SQL file: $sqlFile"
}

$requiredKeys = @("MYSQL_DATABASE", "MYSQL_ROOT_PASSWORD")
$envValues = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    $parts = $trimmed.Split("=", 2)
    if ($parts.Count -eq 2) {
        $envValues[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
}

$missingKeys = @($requiredKeys | Where-Object { -not $envValues.ContainsKey($_) -or -not $envValues[$_] })
if ($missingKeys.Count -gt 0) {
    throw "Missing required .env keys: $($missingKeys -join ', ')"
}
if ($envValues["MYSQL_DATABASE"] -notmatch '^[A-Za-z0-9_]+$') {
    throw "MYSQL_DATABASE may contain only letters, numbers, and underscores."
}

$containerId = docker compose --env-file $envFile -f (Join-Path $projectRoot "docker-compose.yml") ps -q mysql
if (-not $containerId) {
    throw "MySQL container is not running. Run: docker compose up -d mysql"
}

$sql = (Get-Content -LiteralPath $sqlFile -Raw -Encoding UTF8).Replace(
    "{{MYSQL_DATABASE}}",
    $envValues["MYSQL_DATABASE"]
)
$sql |
    docker compose --env-file $envFile -f (Join-Path $projectRoot "docker-compose.yml") exec -T mysql `
        mysql --default-character-set=utf8mb4 -uroot "-p$($envValues['MYSQL_ROOT_PASSWORD'])"

if ($LASTEXITCODE -ne 0) {
    throw "Database initialization failed."
}

Write-Host "Database '$($envValues['MYSQL_DATABASE'])' and table 'cs_qa' are ready."
