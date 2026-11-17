# Voice and export (B4)

`VOICE_ENABLED=on` enables `POST /voice/transcribe` (<=10 MB, magic-byte check, <=60 s; WAV measured up front, other containers by Whisper's reported duration), `POST /voice/speak` (<=1500 chars, voices troy/hannah/austin; split into <=200-char chunks and joined as WAV), `GET /voice/status`. Disabled -> 503 `{"detail":"voice disabled"}`. 10 req/min/user. Audio is never stored.
Export: `POST /export/report` {format: docx|pdf|md, title, markdown, citations} -> download; 2 MB cap, HTML stripped, filenames sanitized.
