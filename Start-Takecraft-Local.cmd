@echo off
setlocal
set "ROOT=%~dp0"
set "OLLAMA_MODELS=%ROOT%.models\ollama"
set "OLLAMA_HOST=127.0.0.1:11434"

if not exist "%ROOT%.tools\ollama\ollama.exe" (
  echo Ollama not found: %ROOT%.tools\ollama\ollama.exe
  pause
  exit /b 1
)

start "Takecraft Ollama" /min /D "%ROOT%.tools\ollama" cmd /k "set OLLAMA_MODELS=%OLLAMA_MODELS% && set OLLAMA_HOST=%OLLAMA_HOST% && ollama.exe serve"
timeout /t 3 /nobreak >nul

start "Takecraft Backend" /D "%ROOT%backend" cmd /k "set FILMAGENT_LLM_BACKEND=openai && set FILMAGENT_LLM_API_KEY=ollama && set FILMAGENT_LLM_BASE_URL=http://127.0.0.1:11434 && set FILMAGENT_LLM_MODEL=qwen3:4b && set FILMAGENT_LLM_REASONING_EFFORT=none && set FILMAGENT_LLM_JSON_MODE=true && set FILMAGENT_IMAGE_BACKEND=mock && set FILMAGENT_VIDEO_BACKEND=mock && set FILMAGENT_VLM_BACKEND=mock && set PATH=%ROOT%.tools\ffmpeg-9.0.1-essentials_build\bin;%%PATH%% && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765"
start "Takecraft Frontend" /D "%ROOT%frontend" cmd /k "set PATH=%ROOT%.tools\node-v24.19.0-win-x64;%%PATH%% && set npm_config_cache=%ROOT%.tools\npm-cache && %ROOT%.tools\node-v24.19.0-win-x64\npm.cmd run dev -- --host 127.0.0.1"

timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:5173"
endlocal
