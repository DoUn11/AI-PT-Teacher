"""테스트가 팀 Firestore에 접속하지 않도록 firebase_admin을 대신하는 메모리 구현."""
import sys
from types import ModuleType

docs: dict[str, dict] = {}


class _Ref:
    def __init__(self, path: str = ""):
        self._path = path

    def collection(self, name: str) -> "_Ref":
        return _Ref(f"{self._path}/{name}" if self._path else name)

    def document(self, doc_id: str) -> "_Ref":
        return _Ref(f"{self._path}/{doc_id}")

    def set(self, data: dict) -> None:
        docs[self._path] = dict(data)


def install() -> None:
    package = ModuleType("firebase_admin")
    package._apps = {}
    package.initialize_app = lambda *args, **kwargs: None
    package.credentials = ModuleType("firebase_admin.credentials")
    package.credentials.Certificate = lambda path: None
    package.firestore = ModuleType("firebase_admin.firestore")
    package.firestore.client = _Ref
    sys.modules["firebase_admin"] = package
    sys.modules["firebase_admin.credentials"] = package.credentials
    sys.modules["firebase_admin.firestore"] = package.firestore
