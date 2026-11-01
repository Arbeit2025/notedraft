import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["NOTEDRAFT_DB"] = _tmp.name

import app as app_module  # noqa: E402


class NoteDraftTest(unittest.TestCase):
    def setUp(self):
        app_module.init_db()
        self.client = app_module.app.test_client()
        with app_module.app.app_context():
            conn = app_module.get_db()
            conn.execute("DELETE FROM drafts")
            conn.execute("DELETE FROM settings")
            conn.commit()

    def test_healthz(self):
        res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "ok")

    def test_index_shows_account_and_warning(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("arbeit2025", body)
        self.assertIn("専用", body)
        self.assertIn("特許", body)

    def test_ideas_three(self):
        ideas = app_module.generate_ideas("死活監視ツール", "個人開発・ツール制作")
        self.assertEqual(len(ideas), 3)
        for idea in ideas:
            self.assertIn("死活監視ツール", idea["title"])
        res = self.client.post(
            "/ideas",
            data={"keyword": "死活監視ツール", "genre": "個人開発・ツール制作",
                  "tone": "カジュアル", "length": "標準", "tags": ["個人開発", "Python"]},
        )
        body = res.get_data(as_text=True)
        self.assertIn("この案で下書きを作る", body)

    def test_draft_contents(self):
        body = app_module.build_draft(
            "死活監視ツールを作ってみた", "死活監視ツール", "個人開発・ツール制作",
            "丁寧", "標準", ["個人開発", "Flask"], "（arbeit2025）",
        )
        self.assertIn("死活監視ツール", body)
        self.assertIn("## ", body)
        self.assertIn("#個人開発", body)
        self.assertIn("#Flask", body)
        self.assertIn("（arbeit2025）", body)
        self.assertGreater(len(body), 200)

    def test_draft_lengths(self):
        short = app_module.build_draft("t", "kw", "", "カジュアル", "短め", [], "")
        long = app_module.build_draft("t", "kw", "", "カジュアル", "長め", [], "")
        self.assertLess(short.count("## "), long.count("## ") + 1)
        self.assertLess(len(short), len(long))

    def test_draft_via_page(self):
        res = self.client.post(
            "/draft",
            data={"title": "直接タイトル", "keyword": "キーワード", "genre": "技術メモ・その他",
                  "tone": "カジュアル", "length": "標準"},
        )
        body = res.get_data(as_text=True)
        self.assertIn("直接タイトル", body)
        self.assertIn("キーワード", body)
        self.assertIn("文字）", body)

    def test_save_list_view_download_delete(self):
        self.client.post(
            "/drafts",
            data={"title": "保存テスト", "body": "# 保存テスト\n\n本文です。", "genre": "個人開発・ツール制作"},
        )
        with app_module.app.app_context():
            conn = app_module.get_db()
            row = conn.execute("SELECT * FROM drafts").fetchone()
            self.assertIsNotNone(row)
            did = row["id"]
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("保存テスト", body)
        body = self.client.get(f"/drafts/{did}").get_data(as_text=True)
        self.assertIn("本文です。", body)
        res = self.client.get(f"/drafts/{did}/download")
        self.assertEqual(res.status_code, 200)
        self.assertIn("本文です。", res.get_data(as_text=True))
        self.assertIn("attachment", res.headers.get("Content-Disposition", ""))
        self.client.post(f"/drafts/{did}/delete")
        self.assertEqual(self.client.get(f"/drafts/{did}").status_code, 404)

    def test_settings_account_and_closing(self):
        self.client.post("/settings", data={"account": "arbeit2025", "closing": "署名テスト"})
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("arbeit2025", body)
        # 締めの定型文が下書き末尾に入る
        res = self.client.post(
            "/draft",
            data={"title": "t", "keyword": "kw", "tone": "カジュアル", "length": "短め"},
        )
        self.assertIn("署名テスト", res.get_data(as_text=True))


class ArticleManagementTest(unittest.TestCase):
    def setUp(self):
        app_module.init_db()
        self.client = app_module.app.test_client()
        with app_module.app.app_context():
            conn = app_module.get_db()
            conn.execute("DELETE FROM drafts")
            conn.execute("DELETE FROM settings")
            conn.execute("DELETE FROM articles")
            conn.execute("DELETE FROM article_stats")
            conn.commit()

    def _add_article(self, title="記事A", published="2026-10-05", genre="個人開発・ツール制作",
                     likes=3, comments=1, views=100):
        return self.client.post("/articles", data={
            "title": title, "url": "https://note.com/arbeit2025/n/abc",
            "published_date": published, "genre": genre,
            "likes": likes, "comments": comments, "views": views,
        })

    def _article_id(self, title="記事A"):
        with app_module.app.app_context():
            conn = app_module.get_db()
            row = conn.execute("SELECT id FROM articles WHERE title = ?", (title,)).fetchone()
            return row["id"]

    def test_article_add_list_edit_delete(self):
        self._add_article()
        body = self.client.get("/articles").get_data(as_text=True)
        self.assertIn("記事A", body)
        self.assertIn("2026-10-05", body)
        aid = self._article_id()
        res = self.client.get(f"/articles/{aid}/edit")
        self.assertEqual(res.status_code, 200)
        self.client.post(f"/articles/{aid}/edit", data={
            "title": "記事A改", "url": "https://note.com/arbeit2025/n/xyz",
            "published_date": "2026-10-06", "genre": "技術メモ・その他",
        })
        body = self.client.get("/articles").get_data(as_text=True)
        self.assertIn("記事A改", body)
        self.assertIn("2026-10-06", body)
        self.assertIn("技術メモ・その他", body)
        self.client.post(f"/articles/{aid}/delete")
        body = self.client.get("/articles").get_data(as_text=True)
        self.assertNotIn("記事A改", body)
        self.assertEqual(self.client.get(f"/articles/{aid}/edit").status_code, 404)

    def test_stats_update_history_and_diff(self):
        self._add_article(likes=3, comments=1, views=100)
        aid = self._article_id()
        self.client.post(f"/articles/{aid}/stats", data={"likes": 3, "comments": 1, "views": 100})
        self.client.post(f"/articles/{aid}/stats", data={"likes": 6, "comments": 2, "views": 150})
        with app_module.app.app_context():
            conn = app_module.get_db()
            hist = conn.execute(
                "SELECT * FROM article_stats WHERE article_id = ? ORDER BY id", (aid,)
            ).fetchall()
            self.assertEqual(len(hist), 2)
            self.assertEqual(hist[0]["checked_date"], app_module.today_str())
            art = conn.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
            self.assertEqual(art["likes"], 6)
            self.assertEqual(art["views"], 150)
        body = self.client.get("/articles").get_data(as_text=True)
        self.assertIn("スキ +3", body)
        self.assertIn("コメント +1", body)
        self.assertIn("ビュー +50", body)
        # 編集ページに履歴が出る
        body = self.client.get(f"/articles/{aid}/edit").get_data(as_text=True)
        self.assertIn("数値の履歴", body)

    def test_dashboard_counts(self):
        today = app_module.datetime.now(app_module.JST)
        this_month = today.strftime("%Y-%m-05")
        self._add_article(title="今月記事", published=this_month, genre="個人開発・ツール制作")
        self._add_article(title="過去記事1", published="2026-09-10", genre="学習・研究メモ")
        self._add_article(title="過去記事2", published="2026-09-20", genre="学習・研究メモ")
        body = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("総記事数：3件", body)
        self.assertIn("の投稿数：1件", body)
        self.assertIn("学習・研究メモ", body)
        self.assertIn("平均投稿間隔", body)
        self.assertIn("最終投稿日", body)

    def test_draft_status_and_publish(self):
        self.client.post("/drafts", data={
            "title": "パイプライン下書き", "body": "# 本文", "genre": "個人開発・ツール制作",
        })
        with app_module.app.app_context():
            conn = app_module.get_db()
            row = conn.execute("SELECT * FROM drafts WHERE title = 'パイプライン下書き'").fetchone()
            did = row["id"]
            self.assertEqual(row["status"], "下書き")
        self.client.post(f"/drafts/{did}/status", data={"status": "案"})
        with app_module.app.app_context():
            conn = app_module.get_db()
            self.assertEqual(
                conn.execute("SELECT status FROM drafts WHERE id = ?", (did,)).fetchone()["status"],
                "案",
            )
        self.client.post(f"/drafts/{did}/publish")
        with app_module.app.app_context():
            conn = app_module.get_db()
            self.assertEqual(
                conn.execute("SELECT status FROM drafts WHERE id = ?", (did,)).fetchone()["status"],
                "公開済み",
            )
            art = conn.execute(
                "SELECT * FROM articles WHERE title = 'パイプライン下書き'").fetchone()
            self.assertIsNotNone(art)
            self.assertEqual(art["published_date"], app_module.today_str())
            self.assertEqual(art["genre"], "個人開発・ツール制作")
            self.assertEqual(art["url"], "")
        body = self.client.get("/articles").get_data(as_text=True)
        self.assertIn("パイプライン下書き", body)
        # ダッシュボードのパイプライン件数にも反映される
        body = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("公開済み", body)


if __name__ == "__main__":
    unittest.main()
