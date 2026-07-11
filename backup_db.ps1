# Database Backup Script for Medical Management System (MongoDB)
# Automatically runs a backup and stores it in the 'backups/' directory.

$BackupDir = Join-Path $PSScriptRoot "backups"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$TargetDir = Join-Path $BackupDir "backup_$Timestamp"

# Ensure backup directory exists
if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}
New-Item -ItemType Directory -Path $TargetDir | Out-Null

Write-Host "==========================================" -ForegroundColor Green
Write-Host "   SYSTEM DATABASE BACKUP (MONGODB)       " -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host "Target Folder: $TargetDir"
Write-Host "Starting backup process..."

$MongodumpPath = Get-Command mongodump -ErrorAction SilentlyContinue

if ($MongodumpPath) {
    Write-Host "Using native mongodump utility..."
    & mongodump --db medical_dossier_db --out $TargetDir
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Backup completed successfully using mongodump!" -ForegroundColor Green
    } else {
        Write-Warning "mongodump failed. Attempting Python fallback..."
        $UsePython = $true
    }
} else {
    Write-Host "mongodump utility not found in PATH. Using Python fallback exporter..."
    $UsePython = $true
}

if ($UsePython) {
    Write-Host "Executing Python MongoDB export..."
    python -c "
import pymongo, os, json
from bson import json_util
try:
    client = pymongo.MongoClient('mongodb://localhost:27017/')
    db = client['medical_dossier_db']
    for col in db.list_collection_names():
        docs = list(db[col].find({}))
        file_path = os.path.join(r'$TargetDir', f'{col}.json')
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(docs, f, default=json_util.default, indent=2)
        print(f'Exported collection: {col} ({len(docs)} documents)')
    print('Backup completed successfully using Python exporter!')
except Exception as e:
    print('Backup failed:', e)
"
}

Write-Host "==========================================" -ForegroundColor Green
