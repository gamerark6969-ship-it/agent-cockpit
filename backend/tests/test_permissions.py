import unittest

from app.permissions import evaluate


class PermissionEvaluationTests(unittest.TestCase):
    def test_unknown_tool_requires_approval(self):
        self.assertEqual(evaluate("unknown_tool", {}, {}), "ask")

    def test_unmatched_bash_command_requires_approval(self):
        settings = {
            "permissions": {
                "bash_rules": [{"match": "git status*", "level": "auto"}]
            }
        }
        self.assertEqual(
            evaluate("bash", {"command": "powershell -EncodedCommand attack"}, settings),
            "ask",
        )

    def test_explicit_safe_bash_rule_is_preserved(self):
        settings = {
            "permissions": {
                "bash_rules": [{"match": "git status*", "level": "auto"}]
            }
        }
        self.assertEqual(
            evaluate("bash", {"command": "git status --short"}, settings), "auto"
        )

    def test_explicit_deny_cannot_be_bypassed_by_safe_defaults(self):
        settings = {
            "permissions": {"tool_levels": {"read_file": "deny"}}
        }
        self.assertEqual(evaluate("read_file", {"path": "README.md"}, settings), "deny")

    def test_exact_rule_wins_over_glob_rule(self):
        settings = {
            "permissions": {
                "tool_levels": {"browser_*": "auto", "browser_submit": "deny"}
            }
        }
        self.assertEqual(evaluate("browser_submit", {}, settings), "deny")

    def test_invalid_bash_level_is_ignored(self):
        settings = {
            "permissions": {
                "bash_rules": [{"match": "*", "level": "unexpected"}]
            }
        }
        self.assertEqual(evaluate("bash", {"command": "whoami"}, settings), "ask")


if __name__ == "__main__":
    unittest.main()
