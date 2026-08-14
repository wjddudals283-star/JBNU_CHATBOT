# 학식 자료를 서버로 밀어넣는다. 작업 스케줄러가 이 파일을 부른다.
#
# ★ 왜 PC 에서 도는가
#   생협 API 가 해외 IP 를 막는다. Render(싱가포르)에서는 403 이 나온다.
#   서버에서 직접 확인했다 — curl 그대로 · 브라우저 UA · UA+Referer 전부
#   403 에 318바이트로 **응답이 한 바이트도 안 변한다.** 헤더는 원인이 아니다.
#   그게 지역 차단인지 데이터센터 IP 차단인지도 구별이 안 된다.
#   가정용 한국 IP 인 이 PC 에는 그 위험이 없다.
#
# ★ 조용히 실패하지 않는다 (오늘 겪은 것)
#   서버에서 coop_week_menu 가 '돌긴 도는데 성공도 실패도 기록이 없는' 상태였다.
#   같은 일이 이 PC 에서 반복되면 안 된다. 그래서 흔적을 세 군데 남긴다.
#     1. logs\push_meal.log        한 줄씩 쌓인다 (지난 이력)
#     2. logs\LAST_RESULT.txt      마지막 결과 한 줄 (지금 상태)
#     3. 바탕화면 경고 파일          실패했을 때만 생기고, 성공하면 지워진다
#   그리고 종료 코드를 그대로 넘긴다 — 작업 스케줄러 '마지막 실행 결과' 칸에 뜬다.

$ErrorActionPreference = 'Stop'

# 이 스크립트가 있는 폴더의 부모 = 저장소 뿌리
$Root    = Split-Path -Parent $PSScriptRoot
$LogDir  = Join-Path $Root 'logs'
$LogFile = Join-Path $LogDir 'push_meal.log'
$LastFile= Join-Path $LogDir 'LAST_RESULT.txt'
$Alert   = Join-Path ([Environment]::GetFolderPath('Desktop')) '★학식_수집_실패.txt'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$Stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'

function Write-Line($text) {
    Add-Content -Path $LogFile -Value $text -Encoding utf8
    Write-Output $text
}

# ── 토큰 확인 ────────────────────────────────────────────────
# ★ 명령행 인자로 받지 않는다. 셸 히스토리와 프로세스 목록에 남는다.
if (-not $env:SKILL_TOKEN) {
    $msg = "$Stamp  실패: SKILL_TOKEN 환경변수가 없습니다"
    Write-Line $msg
    Set-Content -Path $LastFile -Value $msg -Encoding utf8
    $body = @"
학식 자료 수집이 실패했습니다.

원인: SKILL_TOKEN 환경변수가 설정되지 않았습니다.
      (시스템 환경 변수에 넣어야 작업 스케줄러가 볼 수 있습니다)

자세한 내용: $LogFile
시각: $Stamp
"@
    Set-Content -Path $Alert -Value $body -Encoding utf8
    exit 2
}

# ── 파이썬 찾기 ──────────────────────────────────────────────
$Py = $null
foreach ($cand in @('py', 'python')) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if ($c) { $Py = $c.Source; break }
}
if (-not $Py) {
    $msg = "$Stamp  실패: 파이썬을 찾을 수 없습니다"
    Write-Line $msg
    Set-Content -Path $LastFile -Value $msg -Encoding utf8
    Set-Content -Path $Alert -Value "$msg`n`n파이썬이 설치돼 있는지 확인해 주세요." -Encoding utf8
    exit 3
}

# ── 실행 ────────────────────────────────────────────────────
# ★ `& $Py ... 2>&1` 을 쓰면 안 된다 (실행해 보고 알았다)
#   PowerShell 5.1 은 네이티브 exe 의 stderr 를 ErrorRecord 로 감싸는데,
#   $ErrorActionPreference='Stop' 이라 그게 **스크립트를 죽인다.**
#   그러면 로그가 '시작' 에서 끊기고 결과 줄도, 경고 파일도 안 남는다 —
#   우리가 막으려던 '조용히 실패' 그 자체가 된다.
#   파일로 받으면 감싸는 일이 없고 종료 코드도 정확하다.
Write-Line "$Stamp  시작"
$OutTmp = Join-Path $env:TEMP 'jbnu_push_out.txt'
$ErrTmp = Join-Path $env:TEMP 'jbnu_push_err.txt'
# 파이썬 출력 인코딩을 못 박는다. 안 그러면 한글이 깨져서 로그를 못 읽는다.
$env:PYTHONIOENCODING = 'utf-8'

$proc = Start-Process -FilePath $Py `
    -ArgumentList '-m', 'crawler.push', '--source', 'coop_week_menu' `
    -WorkingDirectory $Root -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $OutTmp -RedirectStandardError $ErrTmp
$code = $proc.ExitCode

$out = @()
foreach ($f in @($OutTmp, $ErrTmp)) {
    if (Test-Path $f) {
        $lines = Get-Content $f -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($lines) { $out += $lines }
    }
}

foreach ($line in $out) { Add-Content -Path $LogFile -Value "    $line" -Encoding utf8 }

# ── 결과 남기기 ──────────────────────────────────────────────
if ($code -eq 0) {
    $summary = ($out | Where-Object { $_ -match '성공' } | Select-Object -Last 1)
    $msg = "$Stamp  성공  $summary".TrimEnd()
    Write-Line $msg
    Set-Content -Path $LastFile -Value $msg -Encoding utf8
    # ★ 성공하면 경고 파일을 지운다. 낡은 경고가 남아 있으면
    #   '또 실패했나' 를 매번 사람이 판단하게 된다.
    if (Test-Path $Alert) { Remove-Item $Alert -Force }
} else {
    $msg = "$Stamp  실패 (종료코드 $code)"
    Write-Line $msg
    Set-Content -Path $LastFile -Value $msg -Encoding utf8
    $tail = ($out | Select-Object -Last 6) -join "`n"
    $body = @"
학식 자료 수집이 실패했습니다.

시각: $Stamp
종료코드: $code

마지막 출력:
$tail

전체 기록: $LogFile

★ 챗봇은 이 상태에서도 틀린 답을 하지 않습니다.
  자료가 낡으면 학생에게 "이건 O/O 자료예요" 라고 말합니다.
  다만 최신 식단은 못 보여줍니다.
"@
    Set-Content -Path $Alert -Value $body -Encoding utf8
}

exit $code
