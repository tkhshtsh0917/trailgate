# Trailgate

**Langfuse のエージェント軌跡を、出典を保ったまま収集・監査し、学習用候補へ整理します。**

Trailgate はローカルで動く Python の CLI・ライブラリです。保存済み Observations v2 / Scores v3 応答ページの取り込み、範囲を限定した読み取り専用 API pull、enriched blob export の取り込みに対応します。元データの bytes と hash を保持し、観測できた decision と tool の関係を復元します。構造上の忠実性、品質選択、利用権、プライバシー、データ分割を別々に扱います。本体はモデル API を呼ばず、データセットを送信しません。

[English README](README.md) · [一通りの手順](docs/GUIDE.md) · [v1 の契約と移行](docs/CONTRACT.md) · [ロードマップ](docs/ROADMAP.md) · [公開手順](docs/RELEASING.md)

## 架空データで試す

CPython 3.12.13〜3.14 に対応します。`.python-version` は開発環境用の 3.12.13 を指定しています。リポジトリのルートで実行してください。

```sh
uv sync --locked
uv run --locked trailgate doctor --source langfuse --input examples/synthetic/snapshot
uv run --locked trailgate ingest langfuse-api-snapshot examples/synthetic/snapshot --out private-example
uv run --locked trailgate report private-example
uv run --locked trailgate export graph private-example --out private-graph.json
uv run --locked trailgate evaluate dataset private-example \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json \
  --use internal_training
uv run --locked trailgate export dataset private-example \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json \
  --use internal_training --out private-dataset
```

架空の fixture には Chat SFT 対象の decision が 2 件あります。付属の権利申告とポリシーは、**この架空 snapshot ID にだけ**適用されます。`private-*` は Git から除外し、POSIX 環境では出力ディレクトリを `0700`、ファイルを `0600` で作成します。既存の出力は上書きしません。

従来の `trailgate export sft` は互換性のため残した**候補診断用**です。品質・プライバシー・split は確認しません。`evaluate dataset` はモデル内容を書き出さずに適格性と除外理由を表示します。学習用の出力には `export dataset` を使用してください。詳細は[データセット適格性](docs/DATASET.md)を参照してください。

## 入力と出力

| 入力 | コマンド | 詳細 |
|---|---|---|
| 保存済み Observations v2 / Scores v3 応答ページ | `doctor`、`ingest langfuse-api-snapshot` | [API snapshot 形式](README.md#saved-api-snapshot) |
| 稼働中の Langfuse API | `pull langfuse` | [範囲指定と再開](docs/PULL.md) |
| ローカルに複製した enriched blob export | `ingest-blob` | [manifest に基づく逐次取り込み](docs/BLOB.md) |
| アプリのモデル呼び出し境界 | `CaptureWriter` | [capture と復旧](docs/CAPTURE.md) |

`report` は件数と最大 100 件の decision 診断を既定で表示し、プロンプトや tool 結果の中身は表示しません。`export graph` は、内容を含む非公開の[episode graph](docs/EPISODE_GRAPH.md)を出力します。episode の開始・終了が不明な場合は `unknown` と記録します。`export dataset` は一般形式の episode、条件を満たした Chat SFT 行、出典・選択理由を持つ manifest を非公開ファイルとして出力します。score は後から付く根拠として別に扱い、過去のモデル入力へ混ぜません。

保存済み API snapshot は 64 MiB までです。メモリ上の graph・audit・dataset view は observation 20,000 件、正規化行と capture の合計 32 MiB が上限です。大きな blob archive の `report` と `inspect` は逐次処理し、`inspect` は表示上限による省略件数を明示します。

## 判定の限界

`faithful_candidates` は capture の**構造上の整合性**だけを表し、正答率や利用権の認定ではありません。Chat SFT はテキストと function call の範囲に対応します。provider-native の Responses 記録は元の形で archive と一般形式 episode に残し、Chat へ暗黙に変換しません。

学習用データセットには、generation ID ごとの手動品質選択、task input・model output・tool result それぞれの利用許可、機微情報なしという明示的なプライバシー確認、task 単位の split 記録が必要です。これらは利用者の自己申告であり、Trailgate が品質・法的権利・プライバシーを独立に保証するものではありません。自動墨消し、完全な episode 境界、複数ログソースの取り込み、token 単位の loss mask、RL rollout 出力、モデル学習は v1.0.0 の範囲外です。公開ファイル形式の [JSON Schema](docs/CONTRACT.md#published-json-schemas) と [脅威・失敗境界](docs/SECURITY.md) も参照してください。

[任意の live 診断](examples/live/README.md)は、明示して実行した場合だけ self-hosted Langfuse、モデル API、または MuSiQue の公式 train データを使います。本体とテストは架空データで動き、モデル API を呼びません。

## 開発・検証

```sh
uv run --locked ruff check src tests examples scripts
uv run --locked ruff format --check src tests examples scripts
uv run --locked python -m unittest discover -s tests -q
uv run --locked --extra bulk-parquet python -m unittest discover -s tests -q
uv build
uv run --locked python scripts/check_public_files.py
uv run --locked python scripts/benchmark_bulk.py --rows 100000
```

認証情報ファイル、private archive、実データの軌跡、コーパス本文を Git に追加しないでください。公開ファイル検査は安全性の補助であり、機微情報を全て検出できる保証ではありません。

MIT ライセンスです。[LICENSE](LICENSE)を参照してください。
