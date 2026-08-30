# Record one piece of narration with the Windows synthesiser, capturing where
# each word falls in the audio.
#
# Used by tools/build_narration.py for its `sapi` backend, which exists to
# prove the pipeline without an account or a key. The word timings are the
# point: they are what lets the transcript follow the recording.
param(
  [Parameter(Mandatory)][string]$Text,
  [Parameter(Mandatory)][string]$Wav,
  [Parameter(Mandatory)][string]$Timings,
  [string]$Voice = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { $synth.SelectVoice($Voice) }

$marks = New-Object System.Collections.ArrayList
$subscription = Register-ObjectEvent -InputObject $synth -EventName SpeakProgress -Action {
  $null = $Event.MessageData.Add([pscustomobject]@{
    ms  = [int]$EventArgs.AudioPosition.TotalMilliseconds
    at  = $EventArgs.CharacterPosition
    len = $EventArgs.CharacterCount
  })
} -MessageData $marks

try {
  $synth.SetOutputToWaveFile($Wav)
  $synth.Speak([IO.File]::ReadAllText($Text, [Text.Encoding]::UTF8))
  $synth.SetOutputToNull()
  # The event handlers run on another thread and trail the Speak call.
  Start-Sleep -Milliseconds 400
} finally {
  Unregister-Event -SourceIdentifier $subscription.Name
  $synth.Dispose()
}

# ConvertTo-Json emits a bare object rather than an array of one; the caller
# copes with both.
$marks | ConvertTo-Json -Compress -Depth 3 | Set-Content -Encoding utf8 $Timings
