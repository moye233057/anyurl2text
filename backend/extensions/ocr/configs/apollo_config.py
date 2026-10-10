import threading

import requests

class BaseConfig:
    def __init__(
        self,
        app_id: str = None,
        server_url: str = None,
        cluster: str = "default",
    ):
        self._cache = {}
        self.app_id = app_id
        self.cluster = cluster
        self.server_url = server_url
        self.reload_num = 0
        self.lock = threading.Lock()

    def get_config(self, namespace: str = "application") -> dict:
        """实际直接访问 Apollo 接口的方法。"""
        url = f"{self.server_url}/configfiles/json/{self.app_id}/{self.cluster}/{namespace}?ip="
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        return {}

    def fetch(self, namespace: str = "application"):
        """从 Apollo 拉取配置，并写入当前配置实例属性。当前服务默认不会调用。"""
        if self._cache is None:
            self._cache = self.get_config(namespace)
        else:
            self._cache.update(self.get_config(namespace))

        for field, value in self._cache.items():
            if "[" in str(value) or "(" in str(value):
                value = eval(value)
            setattr(self, field, value)
        self._cache = {}
