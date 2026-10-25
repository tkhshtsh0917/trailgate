# データ保護と失敗時の境界

[English](../SECURITY.md)

Trailgate は prompt、tool 結果、model output を、機微情報を含む可能性があるローカルデータとして扱います。core はモデル呼び出しも dataset upload も行いません。明示的に `pull` した場合のみ Langfuse の GET endpoint を使用します。保存したソースの bytes と内容を含む export はローカルに残り、保持・共有・削除は呼び出し側が管理します。

| 脅威または失敗 | v1 の挙動 | 残る責任 |
|---|---|---|
| credential の漏えい | `pull` の key は process environment から読みます。通常の error と report には表示しません。redirect と周囲の HTTP proxy を無効にし、HTTP は loopback のみに許可します。 | shell history、process environment、host、Langfuse account、network を保護し、信頼できる HTTPS endpoint を使ってください。 |
| Git や package に機微情報が混入 | 非公開出力の名前は Git ignore の対象です。出力には非公開の POSIX mode を使います。`scripts/check_public_files.py` は疑わしい候補 path を内容を読む前に拒否し、公開 text と build member 名を検査します。 | この heuristic ですべての秘密を検出できるわけではありません。stage 済み file と release artifact を手動で確認し、実際の trace、corpus、それらに紐づく申告を commit しないでください。 |
| 誤った学習適格性 | dataset export は構造上の capture、対応する Chat 形式、手動の品質選択、用途申告、プライバシー申告、task split を確認します。根拠がない decision は除外します。 | 申告は権利、正しさ、プライバシー、capture 時点の正確さを証明しません。ソースの規約、入出力、下流の学習設定を確認してください。 |
| 変更された、または不完全なソース | API page、blob object、archive row、出力 hash を各境界で検査します。pull の再開は一致する scope/checkpoint のみ許可し、blob の再開は変更された object を拒否します。 | ソース system は後から到着するデータを欠く場合があります。自己申告された完全な snapshot は、記録された query と収集時点の範囲内に限られます。 |
| resource の枯渇 | API page、snapshot、capture event、JSONL row、メモリ上の view、CLI 出力には文書化した上限があります。大きな archive でも逐次処理の `report`/`inspect` を使えます。 | 大きな graph または dataset の export には、より小さいソース時間範囲が必要です。保持した元データの disk 使用量は運用者が管理してください。 |
| trace 内の悪意ある内容 | trace の text は命令ではなくデータとして扱います。core の変換は tool call を実行せず、Python object を deserialize せず、LLM を呼びません。 | export した text を後でモデルへ渡すアプリには、独自の prompt-injection 対策が必要です。 |

通常の CLI report は未知のソース field 名ではなく件数を表示し、重複 JSON key の error では key の text を伏せます。ソース ID、ローカル path、出力 path は診断に現れる場合があります。機微情報を識別子に含めないでください。JSON Schema の検証は構造上の確認であり、Trailgate の意味上の検証や人間による review の代わりにはなりません。`episodes.jsonl`、graph export、候補 SFT 行は、もっともらしいモデルのやりとりを含んでいても非公開の診断用データです。根拠がない場合は元の archive を保持して調査し、manifest を編集して適格にしてはいけません。

## 依存関係と圧縮 response の対策

core package には実行時依存がありません。commit 済みの `uv.lock` には任意の `bulk-parquet`・`live` extra と開発 group が含まれます。現在の lockfile に `brotli` も `brotlicffi` もありません。したがって CVE-2026-111497 のために Trailgate の Brotli package を更新する必要はありません。`pull` transport は identity encoding を要求し、圧縮された HTTP response は body を読む前に拒否します。Brotli decoder は呼びません。offline の依存関係検査は、将来の lockfile に `brotli<1.2.1` または `brotlicffi<1.2.1.0` が含まれた場合に拒否します。この下限は、確認した [Python advisory](https://github.com/advisories/GHSA-9v8h-qqgm-4m35) と [Brotli の security release](https://github.com/google/brotli/releases/tag/v1.2.1)に基づきます。

CI は変更時および週次に `uv audit --locked` も実行し、lock されたすべての extra と group について既知の advisory を確認します。Dependabot は uv と GitHub Actions の更新を提案します。監査には OSV への network access が必要で、service が利用できなければ失敗します。offline の lockfile 検査はその代わりにはなりません。どちらの検査も、host runtime に内蔵された Brotli、ローカルの Langfuse、無関係な application は対象外です。該当する運用者が別途更新してください。
