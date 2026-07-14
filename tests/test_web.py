from fastapi.testclient import TestClient

from app.main import create_app


def test_home_renders_project_creation_and_list(tmp_path):
    app = create_app(database_path=tmp_path / "web.db")
    with TestClient(app) as client:
        created = client.post(
            "/api/projects", json={"name": "OKX research", "company": "OKX"}
        )
        assert created.status_code == 201
        response = client.get("/")

    assert response.status_code == 200
    assert "创建项目" in response.text
    assert "OKX research" in response.text


def test_project_dashboard_renders_controls_and_analytics(tmp_path):
    app = create_app(database_path=tmp_path / "web-project.db")
    with TestClient(app) as client:
        project = client.post(
            "/api/projects", json={"name": "OKX dashboard", "company": "OKX"}
        ).json()
        response = client.get(f"/projects/{project['id']}")

    assert response.status_code == 200
    assert "OKX dashboard" in response.text
    assert "CSV 导入" in response.text
    assert "运行搜索" in response.text
    assert "统计分布" in response.text
    assert "候选人复核" in response.text
    assert f'window.PROJECT_ID = {project["id"]}' in response.text
