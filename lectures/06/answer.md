# 第6回 解答

## 演習

<!-- [tool call] の行は、表示された行をそのまま貼り付けてください。終わらなかった STEP は、空欄のままでかまいません。 -->

実行環境：Codespaces ではなくローカルの Linux（`uv sync --frozen` 済み）。モデルは設定の既定値 `gemini-3.5-flash-lite`（環境変数 `AIE_MODEL` は未指定）。2026-11-28（JST）に `GEMINI_API_KEY` を設定し、`uv run python -m app.mcp_agent --trace` を実行した。下の [tool call] は、その実行で Gemini が選んだツールと引数である。

- STEP 1 のツールの名前：glossary_search_glossary（`uv run python -m app.agent --list-tools` 側は search_materials）。宿題のツールを足したあとの `app.mcp_agent --list-tools` では、モデルに見えているツールは 9 個（glossary_search_glossary、glossary_get_lecture_summary、glossary_make_quiz、fetch_fetch、aws_ で始まる 5 個）だった
- STEP 2 の [tool call]：`glossary_search_glossary({'query': '3レベル'})` → [tool return] に用語集の「宿題の3レベル」（lecture: 1）が返った。モデルは「基礎・実装・発展の3段階があり、どれか1つに取り組めば完了」と答えた。[usage] 入力 5,711 tokens / 出力 151 tokens / モデル応答 2 回
- STEP 3 の [tool call]：`fetch_fetch({'url': 'https://modelcontextprotocol.io/'})` → [tool error]「Error executing tool fetch: Refused to fetch https://modelcontextprotocol.io/robots.txt: modelcontextprotocol.io resolves to 198.18.0.1, which is not a public address. Start the server with --allow-private-ips to allow private, loopback and link-local addresses.」。1回目は、このツールエラーのあと「モデルが混み合っています（503）」で終了した。約70秒待って同じコマンドを再実行すると、同じ [tool call] と [tool error] のあと、モデルは「URL の内容を直接取得できない」と書いて一般知識で3行に要約した。[usage]（再実行）入力 5,693 tokens / 出力 177 tokens / モデル応答 2 回。一覧には `[server] fetch: コマンドで起動（uvx mcp-server-fetch==2026.11.11 --no-readability-js）` と `fetch_fetch` が出た
- STEP 4 の [tool call]：`aws_aws___search_documentation({'topics': ['general'], 'search_phrase': 'Amazon S3 バージョニング 概要'})` のあと、続けて `aws_aws___search_documentation({'topics': ['general'], 'search_phrase': 'Using versioning in S3 buckets'})`。2回目の [tool return] に "What is Amazon S3?" の "S3 Versioning" の本文が入り、モデルは「同じバケット内で1つのオブジェクトの複数バージョンを保持し、誤削除や上書きから復元できる」と2行で答えた。[usage] 入力 11,645 tokens / 出力 140 tokens / モデル応答 3 回。一覧には `[server] aws: URL に接続（https://knowledge-mcp.global.api.aws）` と aws_ で始まる 5 個のツール（read_documentation、search_documentation、list_regions、get_regional_availability、retrieve_skill）が出た
- 気づき・疑問：
  - 設定ファイルに数行足すだけで、モデルに見えるツールが増える。Gemini は STEP 2 で用語集、STEP 3 で fetch、STEP 4 で AWS の検索を、質問に合わせて自分で選んだ
  - fetch と AWS のツールの説明文は英語で、AWS のものは長く、使い方の指示（どの順で呼ぶかなど）まで説明文に書かれている。サーバーの作者が書いた説明文がそのままモデルに渡るので、足す前に説明文も読む必要がある
  - fetch は、この環境の名前解決が非公開アドレス（198.18.0.1）を返したため、既定の保護（非公開アドレスからは取得しない）で取得を断った。補足にある既定の動作が、実際の Gemini 実行でも働くことを確かめた。`--allow-private-ips` は付けていない
  - STEP 3 の1回目は 503 だった。同じモデル名のまま待って再実行し、2回目は通った

## 宿題

<!-- 基礎・実装・発展のどれか1つに取り組めば完了です。 -->

- 取り組んだレベル：基礎・実装・発展（3つすべて）

### 基礎：get_lecture_summary を公開する

- やったこと：`app/mcp_server.py` の TODO の下に `server.tool()(tools.get_lecture_summary)` を 1 行書き足した（このコミットで変えたのは `app/mcp_server.py` だけ。`app/mcp_servers.json` と `app/mcp_agent.py` は変えていない）
- 結果：
  - `uv run python -m app.mcp_agent --list-tools` の一覧に `glossary_get_lecture_summary`（引数 `{"lecture": {"type": "integer"}}`）が増えた
  - Gemini に「第2回では何を学びますか？」と尋ねると、`glossary_get_lecture_summary({'lecture': 2})` を呼び、[tool return] に `data/lectures/02.md`（第2回 ファインチューニングとLoRA）の内容が返った。モデルは事前学習とファインチューニングの違い、SFT、PEFT と LoRA、QLoRA、過学習への注意、Colab での演習に触れて答えた。[usage] 入力 6,106 tokens / 出力 249 tokens / モデル応答 2 回
  - `uv run pytest -q`：28 passed（下の実装で足したテストを含む）
- 気づき・疑問：エージェントは起動のたびにサーバーへツール一覧を問い合わせるので、サーバー側の 1 行だけで使えるツールが増える。docstring の「「第3回では何を学ぶ？」のように…聞かれたときに使う」がそのまま説明文になり、Gemini もその説明に沿ってこのツールを選んだ

### 実装：make_quiz を MCP サーバーに追加する

- やったこと：
  - この fork の main には第3回の `make_quiz` がなかったため、`app/mcp_server.py` に `@server.tool()` を付けた `make_quiz(lecture: int)` を新しく書いた。用語集（`data/glossary.json`）から指定した回の用語を 1 つ選び、問題と答えを JSON で返す
  - 引数が範囲外のときは例外にせず、直し方が分かる文を `return` で返す。0 以下：「回の番号は1以上の整数で指定してください」、最大の回（第6回）より大きい：「第99回は、この講座の用語集の範囲外です。クイズを作れる回は 第1回、第2回、第3回、第4回、第6回 です。」、用語がない回（第5回）：「第5回の用語は用語集にまだありません」。どの場合も作れる回の一覧を付けた
  - 比較用に、範囲外で `raise` するサーバーをテストの中で起動し、モデルに何が渡るかを確かめた（`tests/test_mcp_homework.py`）
- 結果（Gemini、`gemini-3.5-flash-lite`）：
  - 有効な回（「第2回のクイズを作って」）：`[tool call] glossary_make_quiz({'lecture': 2})` → [tool return] に `{"lecture": 2, "question": "事前学習の意味は？", "answer": "大量の汎用テキストで、次のトークンを予測するようにモデルを学習させる最初の段階。…"}` が返った。モデルは問題だけを出し、答えは書かなかった（指示文の「宿題の答えそのものは書かず、考え方のヒントで導く」に沿っている）。[usage] 入力 5,645 tokens / 出力 95 tokens / モデル応答 2 回
  - 範囲外（「第99回のクイズを作って」）：ツールは呼ばれなかった。モデルは 1 回の応答で「第99回という講義回は存在しません」「特定の回（例：第1回、第2回など）を知らせてほしい」と聞き返した。[usage] 入力 2,778 tokens / 出力 63 tokens / モデル応答 1 回。`return` で用意した「クイズを作れる回は…」という説明文は、この実行ではモデルに渡っていない
  - 比較テスト（偽モデル。`tests/test_mcp_homework.py`）：
    - `return` の場合：`[tool return] glossary_make_quiz -> 第99回は、この講座の用語集の範囲外です。クイズを作れる回は …` と、説明文がそのままモデルに渡った
    - `raise ValueError(...)` の場合：`[tool error] raising_make_quiz -> Error executing tool make_quiz` だけで、例外の文（「第99回は範囲外です」）はモデルに渡らなかった
    - `raise ToolError(...)`（`mcp.server.mcpserver.exceptions`）の場合：`[tool error] raising_make_quiz -> Error executing tool make_quiz: 第99回は範囲外です` と、文も渡った
  - `uv run pytest -q`：28 passed（追加した `tests/test_mcp_homework.py` の 6 件を含む）
- 気づき・疑問：
  - 使っている MCP SDK（`mcp/server/mcpserver/tools/base.py`）は、ふつうの例外を「クラッシュ」と扱い、例外の文をサーバー側にとどめて定型文だけを返す。範囲外のような想定内の誤りは、`return` で説明を返すか、`ToolError` で返さないと、モデルは何を直せばよいか分からない
  - `return` だと正常な結果、`[tool error]` だとモデルが呼び直す扱いになる（pydantic-ai の RetryPromptPart）
  - ただし今回の Gemini は、第99回の質問ではツールを呼ばず、自分の判断で範囲外と伝えて回を聞き返した。説明文を `return` で返す実装は、モデルがツールを呼んだときに効く。呼ぶかどうかはモデル次第である

### 発展：VS Code（GitHub Copilot）の MCP 設定と比べる

- 調べたソフトウェア：VS Code（GitHub Copilot のチャット）。課題の「特に使っているものがない場合は VS Code」に従った
- 調べた日：2026-11-28
- 参照した公式ドキュメント：
  - https://code.visualstudio.com/docs/agent-customization/mcp-servers
  - https://code.visualstudio.com/docs/agents/reference/mcp-configuration
- やったこと：上の 2 ページを読み、4 つの観点で演習の `app/mcp_servers.json` と比べた（VS Code での実際の接続は試していない）

| 観点 | VS Code（Copilot） | 演習の `app/mcp_servers.json` |
|---|---|---|
| 設定を書く場所 | 自分用：`~/.copilot/mcp-config.json`（`COPILOT_HOME` があれば `$COPILOT_HOME/mcp-config.json`）。チーム共有：ワークスペース直下の `.mcp.json`。コマンドパレットの「MCP: Add Server」や「Chat: Open Customizations → MCP Servers」の画面からも登録できる。以前の `.vscode/mcp.json`（`"servers"` 形式）は非推奨の Local harness 用で、新しい設定には使わないと書かれている | リポジトリの `app/mcp_servers.json`。`app/mcp_agent.py` がこのパスを決め打ちで読む |
| コマンドで起動する書き方 | `"mcpServers"` の下に `"type": "stdio"`、`"command"`、`"args"`。必要なら `"env"`、`"cwd"` | `"command"` と `"args"`（`env` も読む）。`"type"` は書かず、`command` があれば stdio と判断する |
| URL で接続する書き方 | `"type": "http"`（Streamable HTTP。古い SSE は `"sse"`）と `"url"`。認証が要るときは `"headers"` や OAuth。秘密の値は `${NAME}` の環境変数参照にし、ファイルに直接書かない | `"url"` だけ。`url` があれば HTTP で接続する。ヘッダーや認証の欄はない |
| 有効になる範囲 | `~/.copilot/mcp-config.json` はその PC のすべてのワークスペース（同じ PC の Copilot CLI にも共通）。`.mcp.json` はそのワークスペースだけで、ソース管理で共有できる | このリポジトリの `app.mcp_agent` を動かしたときだけ |

- 結果：トップレベルが `"mcpServers"` で、サーバー名の下に `command`/`args` か `url` を書く形は同じだった。違いは、VS Code では `type` で接続方式を明示すること、`"tools": ["*"]` のように使うツールを絞る欄があること、認証（headers・OAuth）や作業フォルダ（cwd）の欄があること、置き場所で有効範囲が変わること
- 気づき・疑問：
  - VS Code のドキュメントにも、ローカルの MCP サーバーは自分の PC でコードを実行でき、ツールは自分の権限でサービスにアクセスできるので、提供元・コマンド・接続先を確認してから足すように書かれていた。演習の補足と同じ注意
  - VS Code の中でも、新しい置き場所は `"mcpServers"`、非推奨になった `.vscode/mcp.json` は `"servers"` と、置き場所によってキー名が違う。設定方法は変わるので、調べた日付を残しておく必要がある
