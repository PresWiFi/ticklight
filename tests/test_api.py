import importlib
import os

from fastapi.testclient import TestClient


def make_client(tmp_path, token=""):
    os.environ["DATA_DIR"] = str(tmp_path)
    os.environ["API_TOKEN"] = token
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    import app.config
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.main)
    return TestClient(app.main.app)


def test_api_flow(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/").status_code == 200
        t = c.post("/api/quick", json={"text": "water plants every 3 days !!"}).json()
        assert t["priority"] == 2 and t["recurrence"]["interval"] == 3
        v0 = c.get("/api/version").json()["version"]
        done = c.post(f"/api/tasks/{t['id']}/complete").json()
        assert done["streak"] == 1
        assert c.get("/api/version").json()["version"] > v0
        board = c.get("/api/board").json()
        assert len(board["done_today"]) == 1
        t2 = c.post("/api/tasks", json={"title": "x", "recurrence": {"type": "weekly", "days": [0]},
                                        "due_time": "09:00"}).json()
        assert t2["recurrence_text"] == "weekly on Mon"
        r = c.patch(f"/api/tasks/{t2['id']}", json={"title": "y", "priority": 0}).json()
        assert r["title"] == "y" and r["due_time"] == "09:00"
        assert c.post("/api/tasks", json={"title": "z", "recurrence": {"type": "bad"}}).status_code == 400
        assert c.get("/api/tasks/9999").status_code == 404
        assert c.delete(f"/api/tasks/{t2['id']}").status_code == 200
        assert all(x["id"] != t2["id"] for x in c.get("/api/tasks").json())


def test_api_token(tmp_path):
    with make_client(tmp_path, token="s3cret") as c:
        assert c.get("/api/board").status_code == 401
        assert c.get("/api/board", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/health").status_code == 200
