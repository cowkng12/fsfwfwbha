param(
    [string]$EnvPath = (Join-Path (Split-Path -Parent $PSScriptRoot) ".env"),
    [string]$MrktApiUrl,
    [string]$MrktAuthToken,
    [switch]$ClearMrktAuthToken,
    [string]$TelegramApiId,
    [string]$TelegramApiHash,
    [string]$TelegramPhoneNumber,
    [string]$TelegramSession,
    [switch]$GenerateTelegramSession,
    [switch]$FetchMrktToken,
    [switch]$Prompt,
    [switch]$NoBackup
)

$ErrorActionPreference = "Stop"

function Read-SecretPlain {
    param([string]$Message)

    $secure = Read-Host $Message -AsSecureString
    if ($secure.Length -eq 0) {
        return $null
    }

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Get-EnvValue {
    param(
        [string[]]$Lines,
        [string]$Key
    )

    foreach ($line in $Lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") {
            return $Matches[1]
        }
    }
    return $null
}

function Set-EnvValue {
    param(
        [string[]]$Lines,
        [string]$Key,
        [AllowNull()][string]$Value
    )

    $updated = $false
    $result = @(foreach ($line in $Lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $updated = $true
            "$Key=$Value"
        }
        else {
            $line
        }
    })

    if (-not $updated) {
        $result = @($result) + "$Key=$Value"
    }

    return [string[]]$result
}

function Test-TelegramStringSessionFormat {
    param([string]$Session)

    return [bool]($Session -match '^1[A-Za-z0-9+/=_-]{80,}$')
}

function New-TelegramStringSession {
    param(
        [string]$ApiId,
        [string]$ApiHash,
        [string]$PhoneNumber
    )

    if (-not $ApiId -or -not $ApiHash -or -not $PhoneNumber) {
        throw "TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_PHONE_NUMBER are required to generate TELEGRAM_SESSION."
    }

    $tmpPy = Join-Path ([System.IO.Path]::GetTempPath()) ("tg_session_" + [guid]::NewGuid().ToString("N") + ".py")
    $tmpOut = Join-Path ([System.IO.Path]::GetTempPath()) ("tg_session_" + [guid]::NewGuid().ToString("N") + ".txt")

    $python = @'
import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


async def main():
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    phone = os.environ["TELEGRAM_PHONE_NUMBER"]
    output_file = os.environ["TELEGRAM_SESSION_OUTPUT_FILE"]

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("Telegram login code: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                password = getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)

        session = client.session.save()
        with open(output_file, "w", encoding="utf-8") as handle:
            handle.write(session)
    finally:
        await client.disconnect()


asyncio.run(main())
'@

    try {
        Set-Content -Path $tmpPy -Value $python -Encoding UTF8
        $env:TELEGRAM_API_ID = $ApiId
        $env:TELEGRAM_API_HASH = $ApiHash
        $env:TELEGRAM_PHONE_NUMBER = $PhoneNumber
        $env:TELEGRAM_SESSION_OUTPUT_FILE = $tmpOut

        & python $tmpPy
        if ($LASTEXITCODE -ne 0) {
            throw "Telegram session generation failed. Check that backend dependencies are installed: pip install -r backend/requirements.txt"
        }

        $session = (Get-Content -Path $tmpOut -Raw -Encoding UTF8).Trim()
        if (-not $session) {
            throw "Telegram session generation did not produce a session string."
        }
        if (-not (Test-TelegramStringSessionFormat -Session $session)) {
            throw "Generated TELEGRAM_SESSION does not look like a Telethon StringSession."
        }
        return $session
    }
    finally {
        Remove-Item Env:\TELEGRAM_API_ID -ErrorAction SilentlyContinue
        Remove-Item Env:\TELEGRAM_API_HASH -ErrorAction SilentlyContinue
        Remove-Item Env:\TELEGRAM_PHONE_NUMBER -ErrorAction SilentlyContinue
        Remove-Item Env:\TELEGRAM_SESSION_OUTPUT_FILE -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmpPy -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmpOut -ErrorAction SilentlyContinue
    }
}

function New-MrktAuthToken {
    param(
        [string]$ApiUrl,
        [string]$ApiId,
        [string]$ApiHash,
        [string]$TelegramSession
    )

    if (-not $ApiUrl -or -not $ApiId -or -not $ApiHash -or -not $TelegramSession) {
        throw "MRKT token fetch requires MRKT_API_URL, TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION."
    }

    $tmpPy = Join-Path ([System.IO.Path]::GetTempPath()) ("mrkt_token_" + [guid]::NewGuid().ToString("N") + ".py")
    $tmpOut = Join-Path ([System.IO.Path]::GetTempPath()) ("mrkt_token_" + [guid]::NewGuid().ToString("N") + ".txt")

    $python = @'
import asyncio
import os
from urllib.parse import unquote

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.messages import RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName, InputPeerUser, InputUser


async def main():
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session = os.environ["TELEGRAM_SESSION"]
    api_url = os.environ["MRKT_API_URL"].rstrip("/")
    output_file = os.environ["MRKT_TOKEN_OUTPUT_FILE"]

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")
        resolved = await client(ResolveUsernameRequest("mrkt"))
        bot_user = resolved.users[0]
        bot = InputUser(user_id=bot_user.id, access_hash=bot_user.access_hash)
        peer = InputPeerUser(user_id=bot_user.id, access_hash=bot_user.access_hash)
        app = InputBotAppShortName(bot_id=bot, short_name="app")
        web_view = await client(RequestAppWebViewRequest(peer=peer, app=app, platform="android"))
        init_data = unquote(web_view.url.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion", 1)[0])

    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.post(f"{api_url}/auth", json={"data": init_data})
        response.raise_for_status()
        token = response.json().get("token")

    if not token:
        raise RuntimeError("MRKT auth did not return token")

    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write(token)


asyncio.run(main())
'@

    try {
        Set-Content -Path $tmpPy -Value $python -Encoding UTF8
        $env:TELEGRAM_API_ID = $ApiId
        $env:TELEGRAM_API_HASH = $ApiHash
        $env:TELEGRAM_SESSION = $TelegramSession
        $env:MRKT_API_URL = $ApiUrl
        $env:MRKT_TOKEN_OUTPUT_FILE = $tmpOut

        & python $tmpPy
        if ($LASTEXITCODE -ne 0) {
            throw "MRKT token fetch failed. Verify that TELEGRAM_SESSION is valid and backend dependencies are installed."
        }

        $token = (Get-Content -Path $tmpOut -Raw -Encoding UTF8).Trim()
        if (-not $token) {
            throw "MRKT token fetch did not produce a token."
        }
        return $token
    }
    finally {
        Remove-Item Env:\TELEGRAM_API_ID -ErrorAction SilentlyContinue
        Remove-Item Env:\TELEGRAM_API_HASH -ErrorAction SilentlyContinue
        Remove-Item Env:\TELEGRAM_SESSION -ErrorAction SilentlyContinue
        Remove-Item Env:\MRKT_API_URL -ErrorAction SilentlyContinue
        Remove-Item Env:\MRKT_TOKEN_OUTPUT_FILE -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmpPy -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmpOut -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
    $examplePath = Join-Path (Split-Path -Parent $EnvPath) ".env.example"
    if (Test-Path -LiteralPath $examplePath) {
        Copy-Item -LiteralPath $examplePath -Destination $EnvPath
        Write-Host "Created .env from .env.example"
    }
    else {
        New-Item -ItemType File -Path $EnvPath | Out-Null
        Write-Host "Created empty .env"
    }
}

$lines = [string[]](Get-Content -LiteralPath $EnvPath -Encoding UTF8)

$hasAnyInput = $MrktApiUrl -or $MrktAuthToken -or $ClearMrktAuthToken -or $TelegramApiId -or $TelegramApiHash -or $TelegramPhoneNumber -or $TelegramSession -or $GenerateTelegramSession -or $FetchMrktToken
if (-not $hasAnyInput) {
    $Prompt = $true
}

if ($Prompt) {
    $inputValue = Read-Host "MRKT_API_URL (blank to keep)"
    if ($inputValue) { $MrktApiUrl = $inputValue }

    $inputValue = Read-Host "TELEGRAM_API_ID (blank to keep)"
    if ($inputValue) { $TelegramApiId = $inputValue }

    $inputValue = Read-Host "TELEGRAM_API_HASH (blank to keep)"
    if ($inputValue) { $TelegramApiHash = $inputValue }

    if (-not $GenerateTelegramSession -and -not $FetchMrktToken) {
        $inputValue = Read-SecretPlain "TELEGRAM_SESSION (blank to keep)"
        if ($inputValue) { $TelegramSession = $inputValue }
    }

    if ($GenerateTelegramSession) {
        $inputValue = Read-Host "TELEGRAM_PHONE_NUMBER, international format like +79991234567"
        if ($inputValue) { $TelegramPhoneNumber = $inputValue }
    }

    if (-not $FetchMrktToken -and -not $GenerateTelegramSession) {
        $inputValue = Read-SecretPlain "MRKT_AUTH_TOKEN (blank to keep, type CLEAR to clear)"
        if ($inputValue -eq "CLEAR") {
            $ClearMrktAuthToken = $true
        }
        elseif ($inputValue) {
            $MrktAuthToken = $inputValue
        }
    }
}

$effectiveApiId = if ($TelegramApiId) { $TelegramApiId } else { Get-EnvValue -Lines $lines -Key "TELEGRAM_API_ID" }
$effectiveApiHash = if ($TelegramApiHash) { $TelegramApiHash } else { Get-EnvValue -Lines $lines -Key "TELEGRAM_API_HASH" }
$effectiveTelegramSession = if ($TelegramSession) { $TelegramSession } else { Get-EnvValue -Lines $lines -Key "TELEGRAM_SESSION" }

if ($GenerateTelegramSession) {
    if (-not $TelegramPhoneNumber) {
        $TelegramPhoneNumber = Read-Host "TELEGRAM_PHONE_NUMBER, international format like +79991234567"
    }
    $TelegramSession = New-TelegramStringSession -ApiId $effectiveApiId -ApiHash $effectiveApiHash -PhoneNumber $TelegramPhoneNumber
}

if ($FetchMrktToken) {
    if (-not $MrktApiUrl) {
        $MrktApiUrl = Get-EnvValue -Lines $lines -Key "MRKT_API_URL"
    }
    if (-not $TelegramSession) {
        $TelegramSession = $effectiveTelegramSession
    }
    if (-not (Test-TelegramStringSessionFormat -Session $TelegramSession)) {
        throw "TELEGRAM_SESSION in .env is not a valid Telethon StringSession. Run with -GenerateTelegramSession first."
    }
    $MrktAuthToken = New-MrktAuthToken -ApiUrl $MrktApiUrl -ApiId $effectiveApiId -ApiHash $effectiveApiHash -TelegramSession $TelegramSession
}

if (-not $NoBackup) {
    $backupPath = "$EnvPath.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $EnvPath -Destination $backupPath
    Write-Host "Backup written to $backupPath"
}

if ($MrktApiUrl) { $lines = Set-EnvValue -Lines $lines -Key "MRKT_API_URL" -Value $MrktApiUrl }
if ($TelegramApiId) { $lines = Set-EnvValue -Lines $lines -Key "TELEGRAM_API_ID" -Value $TelegramApiId }
if ($TelegramApiHash) { $lines = Set-EnvValue -Lines $lines -Key "TELEGRAM_API_HASH" -Value $TelegramApiHash }
if ($TelegramSession) { $lines = Set-EnvValue -Lines $lines -Key "TELEGRAM_SESSION" -Value $TelegramSession }

if ($ClearMrktAuthToken) {
    $lines = Set-EnvValue -Lines $lines -Key "MRKT_AUTH_TOKEN" -Value ""
}
elseif ($MrktAuthToken) {
    $lines = Set-EnvValue -Lines $lines -Key "MRKT_AUTH_TOKEN" -Value $MrktAuthToken
}

Set-Content -LiteralPath $EnvPath -Value $lines -Encoding UTF8

$currentMrktToken = Get-EnvValue -Lines $lines -Key "MRKT_AUTH_TOKEN"
if ($TelegramSession -and $currentMrktToken -and -not $MrktAuthToken -and -not $ClearMrktAuthToken) {
    Write-Warning "TELEGRAM_SESSION was changed, but MRKT_AUTH_TOKEN is still set. Use -ClearMrktAuthToken to force fresh MRKT auth from Telegram."
}

Write-Host "Updated $EnvPath"
