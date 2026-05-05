# Richat DataBridge Web

Frontend Django pour Richat DataBridge.

## Lancement local

Terminal 1, depuis `C:\Users\medya\OneDrive\Desktop\Stage` :

```powershell
py -3 -m uvicorn databridge_api.app.main:app --reload --port 8001
```

Terminal 2, depuis `C:\Users\medya\OneDrive\Desktop\Stage\databridge_web` :

```powershell
py -3 -m pip install -r requirements.txt
$env:FASTAPI_BASE_URL = "http://127.0.0.1:8001"
py -3 manage.py runserver 127.0.0.1:8000
```

Ouvrir ensuite :

```text
http://127.0.0.1:8000
```

## Configuration

Le frontend lit l'URL du backend via :

```text
FASTAPI_BASE_URL=http://127.0.0.1:8001
```

Si cette variable n'est pas définie, Django utilise `http://127.0.0.1:8001` par défaut.
