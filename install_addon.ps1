$ErrorActionPreference = 'Stop'

$source = 'E:\UE\GroomSegmentExporter\groom_segment_exporter'
$target = Join-Path $env:APPDATA 'Blender Foundation\Blender\5.1\scripts\addons\groom_segment_exporter'

function From-Utf8Base64($value) {
    return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($value))
}

New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force

Write-Host "$((From-Utf8Base64 '5bey5a6J6KOFIEJsZW5kZXJHcm9vbVRvVUUg5Yiw77ya'))$target"
Write-Host (From-Utf8Base64 '6K+35ZyoIEJsZW5kZXIg5Lit5ZCv55So77ya57yW6L6RID4g5YGP5aW96K6+572uID4g5o+S5Lu2ID4gQmxlbmRlckdyb29tVG9VRQ==')
