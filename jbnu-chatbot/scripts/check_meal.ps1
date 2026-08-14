# 학식 자료가 잘 올라가고 있는지 **서버에 물어본다.**
#
# ★ PC 로그만 보면 안 되는 이유
#   작업이 아예 안 돌았으면 PC 에는 **아무 기록도 안 남는다.**
#   없는 것을 알아채는 건 사람에게 제일 어려운 일이다.
#   그래서 '마지막으로 성공한 게 언제인가' 는 서버가 답하게 한다.
#   오늘 서버에서 겪은 게 정확히 그 상태였다 — 돌긴 도는데 기록이 없었다.

$ErrorActionPreference = 'Stop'

$Server = if ($env:JBNU_SERVER) { $env:JBNU_SERVER } else { 'https://jbnu-chatbot.onrender.com' }
$Root   = Split-Path -Parent $PSScriptRoot
$LastFile = Join-Path $Root 'logs\LAST_RESULT.txt'

Write-Output ''
Write-Output '================ 학식 자료 상태 ================'

# ── 1. 이 PC 가 마지막으로 한 일 ─────────────────────────────
if (Test-Path $LastFile) {
    Write-Output ''
    Write-Output "이 PC 의 마지막 실행"
    Write-Output ("  " + (Get-Content $LastFile -Raw).Trim())
} else {
    Write-Output ''
    Write-Output "이 PC 의 마지막 실행"
    Write-Output "  기록 없음 — 아직 한 번도 안 돌았습니다"
}

# ── 2. 서버가 아는 진실 ──────────────────────────────────────
if (-not $env:SKILL_TOKEN) {
    Write-Output ''
    Write-Output "서버 상태를 보려면 SKILL_TOKEN 환경변수가 필요합니다."
    Write-Output "(설정 방법은 docs\PC_SCHEDULER.md 2단계)"
    exit 2
}

try {
    $r = Invoke-RestMethod -Uri "$Server/admin/status" -Method Get `
         -Headers @{ 'X-Skill-Token' = $env:SKILL_TOKEN } -TimeoutSec 60
} catch {
    Write-Output ''
    $m = $_.Exception.Message
    if ($m -match '401') {
        # ★ '권한이 없음' 만 보여주면 뭘 고쳐야 할지 알 수가 없다
        Write-Output "서버가 토큰을 거부했습니다 (401)."
        Write-Output "  SKILL_TOKEN 값이 Render 에 있는 값과 다릅니다."
        Write-Output "  docs\PC_SCHEDULER.md 2단계를 다시 봐 주세요."
    } else {
        Write-Output "서버에 물어보지 못했습니다: $m"
    }
    exit 3
}

$coop = $r.sources | Where-Object { $_.source_key -eq 'coop_week_menu' }

Write-Output ''
Write-Output "서버가 아는 것  (서버 시각 $($r.now_kst))"
if (-not $coop) {
    Write-Output "  생협 식단 항목을 찾지 못했습니다"
    exit 4
}

if ($coop.last_success) {
    $age = [math]::Round($coop.age_hours, 1)
    Write-Output "  마지막 성공  $($coop.last_success)   ($age 시간 전)"
} else {
    Write-Output "  마지막 성공  없음 — 한 번도 못 받았습니다"
}

Write-Output ''
if ($coop.stale -or -not $coop.last_success) {
    Write-Output '  ★ 자료가 오래됐습니다.'
    Write-Output '    학생에게는 "이건 O/O 자료예요" 라고 나갑니다 — 틀린 답은 안 나갑니다.'
    Write-Output '    다만 최신 식단은 못 보여줍니다.'
    Write-Output ''
    Write-Output '    할 일: 이 폴더의  [학식_지금_보내기.bat]  을 한 번 눌러 주세요.'
    exit 1
}

Write-Output '  ✅ 정상입니다. 최신 식단이 나가고 있습니다.'
exit 0
