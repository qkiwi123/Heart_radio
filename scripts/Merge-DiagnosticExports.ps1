[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
foreach($taskGroup in @('logs','rfcomm-probe','capability-probe')){
    $taskSource=Join-Path (Join-Path $OutputDirectory 'segments') $taskGroup
    if(!(Test-Path -LiteralPath $taskSource -PathType Container)){continue}
    $taskRows=@()
    foreach($taskFile in Get-ChildItem -LiteralPath $taskSource -File){
        if($taskFile.Name -notmatch '^(?<base>.+?\.jsonl)(\.part-(?<stamp>[0-9]+)-(?<seq>[0-9]+)\.jsonl)?$'){continue}
        $taskBase=$Matches.base
        $taskStamp=if($Matches.stamp){[long]$Matches.stamp}else{[long]::MaxValue}
        $taskSequence=if($Matches.seq){[long]$Matches.seq}else{0L}
        $taskValid=if($taskGroup -eq 'logs'){$taskBase -match '^(session-[0-9-]+|lifecycle)\.jsonl$'}elseif($taskGroup -eq 'rfcomm-probe'){$taskBase -eq 'events.jsonl'}else{$taskBase -in @('requests.jsonl','bridge-events.jsonl')}
        if($taskValid){$taskRows+=@{base=$taskBase;stamp=$taskStamp;sequence=$taskSequence;path=$taskFile.FullName}}
    }
    $taskDestination=if($taskGroup -eq 'logs'){$OutputDirectory}else{Join-Path $OutputDirectory $taskGroup}
    New-Item -ItemType Directory -Path $taskDestination -Force | Out-Null
    foreach($taskSet in ($taskRows|Group-Object { $_.base })){
        $taskTarget=Join-Path $taskDestination $taskSet.Name
        $taskOutput=[IO.File]::Open($taskTarget,[IO.FileMode]::Create,[IO.FileAccess]::Write)
        try{
            foreach($taskRow in ($taskSet.Group|Sort-Object @{Expression={$_.stamp}},@{Expression={$_.sequence}})){
                $taskInput=[IO.File]::OpenRead($taskRow.path)
                try{$taskInput.CopyTo($taskOutput)}finally{$taskInput.Dispose()}
            }
        }finally{$taskOutput.Dispose()}
    }
}
