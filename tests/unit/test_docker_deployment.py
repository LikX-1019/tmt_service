from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_container_binds_to_all_interfaces() -> None:
    """服务器部署时容器端口必须能被 Docker 发布到宿主机。"""
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '--host", "0.0.0.0"' in dockerfile
    assert '--host", "127.0.0.1"' not in dockerfile


def test_image_build_context_excludes_secrets_and_local_state() -> None:
    """Docker 构建上下文不能携带本地密钥、日志、运行数据或模型文件。"""
    lines = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    }

    required_excludes = {".env", ".env.*", "data/", "logs/", "model/"}
    assert required_excludes <= lines
