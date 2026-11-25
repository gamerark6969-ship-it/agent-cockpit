param([Parameter(Mandatory=$true)][string]$Payload)
$ErrorActionPreference = "Stop"
$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice.Rate = 1
    $voice.SetOutputToWaveFile([string]$data.output)
    $voice.Speak([string]$data.text)
} finally {
    $voice.Dispose()
}
