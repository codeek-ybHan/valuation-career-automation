"""Gmail refresh token 1회 발급 스크립트 (로컬에서만 실행).

사전 준비
  1. Google Cloud Console → 프로젝트 생성 → Gmail API 사용 설정
  2. OAuth 동의 화면 구성 (테스트 사용자에 본인 Gmail 추가)
  3. 사용자 인증 정보 → OAuth 클라이언트 ID → 유형 "데스크톱 앱" → JSON 다운로드

실행
  pip install google-auth-oauthlib
  python scripts/get_gmail_refresh_token.py path/to/client_secret.json

출력된 값을 .env / GitHub Secrets의 GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
GOOGLE_REFRESH_TOKEN에 넣는다. (이 스크립트는 운영 의존성이 아니라 requirements.txt에 없다.)
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]  # 발송 권한만 요청 (최소 권한)

if len(sys.argv) != 2:
    sys.exit(__doc__)

flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
# access_type=offline + prompt=consent 가 있어야 refresh token이 항상 발급된다
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\nGOOGLE_CLIENT_ID=" + creds.client_id)
print("GOOGLE_CLIENT_SECRET=" + creds.client_secret)
print("GOOGLE_REFRESH_TOKEN=" + creds.refresh_token)
