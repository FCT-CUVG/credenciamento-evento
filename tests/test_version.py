"""Versão do sistema: leitura do git describe e exibição só para a coordenação."""
import unittest
from unittest.mock import patch

from credenciamento import version
from support import CredenciamentoTestCase


class VersionTest(CredenciamentoTestCase):
    def test_labels(self):
        cases = {"v0.1.0": "v0.1.0", "v0.1.0-3-gabc1234": "v0.1.0 + 3 commits (abc1234)",
                 "v1.2.0-rc.1-1-gdeadbee": "v1.2.0-rc.1 + 1 commit (deadbee)", "abc1234": "commit abc1234",
                 "desconhecida": "desconhecida", "": "desconhecida"}
        for described, expected in cases.items():
            self.assertEqual(version.label(version.parse(described)), expected, described)

    def test_dashboard_shows_version_to_admin_only(self):
        version.current.cache_clear()
        self.addCleanup(version.current.cache_clear)
        with patch.object(version, "_describe", return_value="v0.1.0-2-gabc1234"):
            code, data, _ = self.request("/api/dashboard", cookie=self.login("admin")[0])
        self.assertEqual((code, data["version"]["tag"], data["version"]["label"]),
                         (200, "v0.1.0", "v0.1.0 + 2 commits (abc1234)"))
        self.assertIn(self.request("/api/dashboard", cookie=self.login("vol1")[0])[0], (401, 403))
        self.assertNotIn("version", self.request("/api/dashboard/summary")[1])


if __name__ == "__main__":
    unittest.main()
