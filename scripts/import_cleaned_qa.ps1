param(
    [string]$Source = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env"
$composeFile = Join-Path $projectRoot "docker-compose.yml"
$sourceFile = Join-Path $projectRoot $Source
$generator = Join-Path $PSScriptRoot "import_cleaned_qa.py"
$tempSql = Join-Path $projectRoot ".codex-tmp\qa_import.sql"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing .env file: $envFile"
}
if ([string]::IsNullOrWhiteSpace($Source)) {
    throw "Provide a reviewed QA workbook with -Source. The old JAFFICK dataset has been removed."
}
if (-not (Test-Path -LiteralPath $sourceFile)) {
    throw "Missing source workbook: $sourceFile"
}

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

$requiredKeys = @("MYSQL_DATABASE", "MYSQL_ROOT_PASSWORD")
$missingKeys = @($requiredKeys | Where-Object { -not $envValues.ContainsKey($_) -or -not $envValues[$_] })
if ($missingKeys.Count -gt 0) {
    throw "Missing required .env keys: $($missingKeys -join ', ')"
}
if ($envValues["MYSQL_DATABASE"] -notmatch '^[A-Za-z0-9_]+$') {
    throw "MYSQL_DATABASE may contain only letters, numbers, and underscores."
}

$containerId = docker compose --env-file $envFile -f $composeFile ps -q mysql
if (-not $containerId) {
    throw "MySQL container is not running. Run: docker compose up -d mysql"
}

$batchNo = "QA-" + (Get-Date -Format "yyyyMMdd-HHmmss")
python $generator $sourceFile $tempSql --batch-no $batchNo
if ($LASTEXITCODE -ne 0) {
    throw "QA workbook validation failed."
}

Get-Content -LiteralPath $tempSql -Raw -Encoding UTF8 |
    docker compose --env-file $envFile -f $composeFile exec -T mysql `
        sh -lc 'mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
if ($LASTEXITCODE -ne 0) {
    throw "QA database import failed."
}

Write-Host "QA import completed. Batch: $batchNo"
