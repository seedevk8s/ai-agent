# Frodo Knowledge Agent Front

`DESIGN.md`의 Cognitive Warmth 디자인을 적용한 파일 기반 RAG Agent 웹 콘솔입니다.

- PDF, DOCX, TXT, Markdown, CSV 드래그앤드롭 업로드
- 실제 Qdrant 문서 목록 및 삭제
- 출처 컨텍스트를 포함한 Agent 질의응답
- 브라우저 로컬 대화 히스토리

```powershell
cd front
python -m http.server 3000
```

Kubernetes에서는 프론트 Nginx가 `/api`를 `rag-app-service:8000`으로 전달합니다. 다음 명령 하나만 실행한 후 `http://localhost`를 엽니다.

```powershell
kubectl port-forward svc/rag-front-service 80:80
```

프론트는 API 주소를 동일 오리진의 `/api`로 고정하며, 좌측 **API 문서** 메뉴는 프록시된 Swagger UI(`/api/docs`)를 새 탭에서 엽니다.

단순 정적 서버로 프론트를 단독 실행하면 `/api` 프록시가 없으므로 질의와 문서 기능은 동작하지 않습니다. 전체 기능은 Kubernetes의 프론트 Nginx를 통해 실행합니다.

## Docker 실행

저장소 루트에서 다음 명령을 실행합니다.

```powershell
docker build -f Docker/front/Dockerfile -t orbit-rag-front:latest .
docker run --rm -p 3000:80 --name orbit-rag-front orbit-rag-front:latest
```

브라우저에서 `http://localhost:3000`으로 접속합니다.
