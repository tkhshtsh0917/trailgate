# Trailgate

**保存済みLangfuse記録を監査し、出典を追えるChat SFT候補を作成します。**

[English README](README.md) · [v1.0.0までのロードマップ](docs/ROADMAP.md)

現在のv0.1.0は、ローカルで動く初期版です。[Langfuse Observations API v2／Scores API v3](https://langfuse.com/docs/api-and-data-platform/features/public-api)の**保存済み応答ページ**を読み、元データのハッシュと照合可能なprivate archiveを作ります。Langfuse上の`input`がモデルへ渡った完全な入力とは限らないため、任意の`CaptureWriter`でモデル直前のmessages、利用可能tool、実際に渡したtool結果を別記録できます。

本体のCLI・ライブラリはモデルAPIやネットワークに接続しません。有料モデルを使う実行例は`examples/`に分離し、テストでは動かしません。

`src/trailgate/`は本体、`examples/synthetic/`は架空データ、`examples/live/`は明示実行する診断例、`tests/`はオフラインテスト、`docs/`はロードマップと公開手順です。

## 架空データで試す

リポジトリのルートで[uv](https://docs.astral.sh/uv/)を使います。Python 3.12.13を`.python-version`で指定しています。

```sh
uv sync --locked
uv run --locked trailgate doctor --source langfuse --input examples/synthetic/snapshot
uv run --locked trailgate ingest langfuse-api-snapshot examples/synthetic/snapshot --out private-example
uv run --locked trailgate report private-example
uv run --locked trailgate inspect private-example --trace trace-example
uv run --locked trailgate export steps private-example --out private-steps.jsonl
uv run --locked trailgate export sft private-example --rights-file examples/synthetic/rights.json --use internal_training --out private-sft.jsonl
```

`private-*`はGitの対象から除外します。archive directoryは`0700`、作成するファイルは`0600`に設定し、既存の出力先を上書きしません。出力先を共有する前に内容を確認してください。fixtureの利用許可ファイルは、この**架空データのsnapshot IDだけ**に適用できます。元のページやcaptureを変更した場合は、新しいsnapshotに対する利用許可を確認して作成してください。

## 監査で分かること

- 元のAPI応答ページを保存し、source row ID・hashと正規化後の内容を対応付けます。
- generationごとに、モデル直前の入力、tool schema、assistant出力、対応するtool結果が記録されているか確認します。Chat SFT候補はテキストとfunction-callの形式を検査します。欠損を空のtool一覧や空の結果として補いません。
- `faithful_candidates`は記録の構造的な整合性を表します。回答品質、学習の効果、データの利用権の認定ではありません。
- Chat形式のSFT出力には、archiveの`snapshot_id`に一致する利用条件の自己申告が必要です。申告の真偽はTrailgateでは検証できません。provider-nativeのResponses記録は保持・監査しますが、現行のChat SFT出力へ暗黙に変換しません。

v0.1.0にはLangfuseからの直接取得、複数traceをまたぐepisode復元、scoreの意味的な帰属、機密情報の自動検出はありません。大規模収集やモデル学習の成果も主張しません。初期版の範囲と後続の合格条件は[ロードマップ](docs/ROADMAP.md)を参照してください。

## 開発・確認

```sh
uv run --locked ruff check src tests examples scripts
uv run --locked ruff format --check src tests examples scripts
uv run --locked python -m unittest discover -s tests -q
uv build
uv run --locked python scripts/check_public_files.py
```

公開候補のファイル名に秘密情報らしいものがある場合、公開検査は**中身を開かずに**失敗します。実データや環境変数ファイルはリポジトリへ追加しないでください。

MITライセンスです。[LICENSE](LICENSE)を参照してください。
