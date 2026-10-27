import os
import tempfile
import unittest
from unittest import mock

import pages


class BuildPageTest(unittest.TestCase):
    def test_includes_config_and_channel_are_filled_in(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "p.html"), "w", encoding="utf-8") as fh:
                fh.write("<script>/*QWEBCHANNEL*/</script><script>/*CONFIG*/</script>"
                         "<script>/*INCLUDE:a.js*/</script>")
            with open(os.path.join(folder, "a.js"), "w", encoding="utf-8") as fh:
                fh.write("var a = 1; // \\1 stays as written")
            with mock.patch.object(pages, "WEB_DIR", folder):
                html = pages.build_page("p.html", {"x": [1, 2]}, "QWC();")
        self.assertIn("<script>QWC();</script>", html)
        self.assertIn('window.CONFIG = {"x": [1, 2]};', html)
        self.assertIn("var a = 1; // \\1 stays as written", html)

    def test_the_real_map_page_builds(self):
        html = pages.build_page("map.html", {"x": 1}, "QWC();")
        self.assertNotIn("/*INCLUDE:", html)
        self.assertIn("function setVerdicts", html)
