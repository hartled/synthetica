# --proxy-headers so the edge scheme is https. Report links use PUBLIC_BASE_URL.
# Rate limits use CF-Connecting-IP when RENDER=true.
web: uvicorn app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
