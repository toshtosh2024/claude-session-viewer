# Claude Session Viewer

A local-only tool for browsing and managing Claude Code session records (the conversation history shown under `/resume`) in a browser, and moving selected sessions to the trash.

## Features

- Lists all sessions under `~/.claude/projects` in newest-first order, showing the title, first message, folder, round-trip count, and size
- Search and filter by folder or keyword
- Full conversation view with toggles for tool calls, thoughts, and system messages
- Multi-select deletion. Selected conversation records, sub-agent sessions, `file-history`, and `session-env` are moved to `~/.Trash` together so they can be restored from the trash
- Running sessions cannot be deleted

## Usage

Python 3 (standard library only) is required. Intended for macOS.

```bash
python3 server.py            # Opens the browser automatically
python3 server.py --port 9000 --no-open
```

If you want to use it as a command:

```bash
cat > ~/.local/bin/claude-sessions <<'SH'
#!/bin/bash
exec python3 "$HOME/dev/claude-session-viewer/server.py" "$@"
SH
chmod +x ~/.local/bin/claude-sessions
```

## Security

- Listens only on `127.0.0.1`
- The API validates an auto-generated token and the `Host` header for each run, so it cannot be read or deleted by other sites open in the browser

---

Japanese note / 追記:

# Claude Session Viewer

Claude Code のセッション（`/resume` に出る会話履歴）をブラウザで一覧・閲覧し、選んだものをゴミ箱に移動するローカル専用ツール。

## 機能

- `~/.claude/projects` 以下の全セッションを新しい順に一覧表示（タイトル・最初の発言・フォルダ・往復数・サイズ）
- 検索・フォルダで絞り込み
- 会話の全文表示（ツール呼び出し・思考・システムメッセージは切り替え式）
- 複数選択して削除。会話本体・サブエージェント・`file-history`・`session-env` をまとめて `~/.Trash` に移動するので、ゴミ箱から戻せる
- 実行中のセッションは削除できない

## 使い方

Python 3（標準ライブラリのみ）が必要。macOS 向け。

```bash
python3 server.py            # ブラウザが自動で開く
python3 server.py --port 9000 --no-open
```

コマンドとして使う場合:

```bash
cat > ~/.local/bin/claude-sessions <<'SH'
#!/bin/bash
exec python3 "$HOME/dev/claude-session-viewer/server.py" "$@"
SH
chmod +x ~/.local/bin/claude-sessions
```

## セキュリティ

- `127.0.0.1` のみで待ち受け
- API は起動ごとに生成されるトークンと `Host` ヘッダーを検証するため、ブラウザで開いている他のサイトからは読み取り・削除できない
