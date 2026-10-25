# Trailgate のリリースマイルストーン

[English](../ROADMAP.md)

更新日: 2026-10-25。これは **library と CLI としての Trailgate** のリリース記録です。検索モデルの学習や実際の軌跡データの公開には依存しません。ローカル commit、GitHub への push、version tag、package release は別々の操作です。`v1.0.0` は tag 済みですが、package index には公開していません。

## v1.0.0 の製品契約

Trailgate は、利用者自身の Langfuse project から軌跡を準備します。範囲を限定したソース snapshot を取得または取り込み、ソースの根拠を保持し、観測可能な agent run を復元して、どの decision を指定された学習用 view に export できるか説明します。有用な結果は単なる JSON 変換ではなく、**理由を伴い再現可能な適格性判定**です。

v1 の対応ソースは Langfuse です。読み取りの契約は [Observations API v2 と Scores API v3](https://langfuse.com/docs/api-and-data-platform/features/public-api)、bulk 取り込み用には完了した [enriched blob export manifest](https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage) です。Langfuse は [observation、trace、session](https://langfuse.com/docs/observability/data-model) を区別します。Trailgate はいずれも完全な episode と暗黙に同一視しません。実際に検証した server と SDK の version は compatibility matrix に示します。Langfuse Python SDK v4 と v5 は score の読み取り accessor が異なるため、collector は差異を adapter 内で扱うか、安定した REST endpoint を使う必要があります。

v1 の出力は、(1) 変更できないローカルのソース snapshot、(2) ソース参照を持つ version 付きの episode/decision/action/observation view、(3) 構造上の忠実性、品質 signal、権利、欠落した根拠を分ける audit report、(4) 任意で作る一般 episode と**対応形式**の SFT export です。Langfuse の score は対象に付いた signal であり、すべての action が正しい証明ではありません。provider 固有の Responses capture を自動的に Chat SFT の例とは扱いません。

core package はモデル実行、fine-tuning、回答品質の判定、学習 provider への dataset 送信を行いません。trace だけでモデルに見えた context を正確に表すとも主張しません。Codex/Claude Code の log の直接取り込み、複数ソースの connector、preference/RL にそのまま使える export、法的権利の自動確認は v1 の対象外です。

## マイルストーン

| マイルストーン | 成果物 | 完了条件 |
|---|---|---|
| **v0.1.0 — 誤解のない公開 preview** | offline snapshot importer、capture helper、decision audit、架空 fixture を提供します。CLI command、対応する入力形式、既知の対象外事項、既定で非公開の挙動を正確に記述します。任意の model 例は本体から分けます。 | 新しい checkout で install、test、fixture 実行、sdist/wheel build ができます。候補 artifact と Git tree に `.env`、非公開 archive、dataset text、credential がありません。README が episode の連結や online pull を実装済みと誤解させません。 |
| **v0.2.0 — 軌跡 pipeline の拡張** | episode graph、capture 復旧、範囲付きの読み取り専用 Langfuse pull、再開可能な enriched blob 取り込み、gate 付き dataset export を加えます。元の根拠を保持し、構造上の忠実性、手動の品質選択、権利、プライバシー、split の出典を分けます。 | 架空データでの対抗的・pagination test、self-hosted 環境からの架空 telemetry pull、決定的な dataset 出力、1 GiB 未満での 100,000 observation 取り込みが通ります。不明な model context と episode 境界は明示したままです。 |
| **v1.0.0 のソース候補** | 文書化された Python/CLI 契約を固定し、JSON Schema と移行 guide を公開し、上限のある conformance と release gate を検証します。 | 新しい利用者がモデルを実行せずに Langfuse データを収集または取り込み、適格性を確認し、許可された view を export できます。build artifact には意図した公開ファイルだけが含まれます。 |
| **v1.0.0 tag** | ソース review と CI 成功後に作成しました。package index での公開は別の判断です。 | tag は review 済み commit `9643b97` を指します。 |

マイルストーンは日付ではなく gate です。preview では限界を明示できます。v1.0.0 には信頼性と契約の gate を要求しました。機能を pre-1.0 version 間で移動しても、出典、完全性、プライバシー、再現性の完了条件は維持します。

## v1.0.0 の検証

v0.2.0 では graph、capture 復旧、読み取り専用の v2/v3 pull、再開可能な enriched blob 取り込み、gate 付き dataset export を実装しました。v1 のソース候補では、経路横断の conformance suite、上限のあるメモリ上の view と CLI 出力、適格性の事前確認、公開 JSON Schema、[契約・移行・互換性](CONTRACT.md)を追加しました。架空 fixture は対抗的な capture 状態、checkpoint 復旧、score の改訂、split 間の漏れを扱います。ローカルの self-hosted Langfuse の trace と score をモデル呼び出しなしで pull し、offline import と比較しました。100,000 observation の resource gate が対象とするのは取り込みと概要出力のみです。大規模な graph と dataset export は文書化した上限で明示的に失敗し、逐次処理の診断は引き続き利用できます。

review 済みの `v1.0.0` tag は commit `9643b97` を指し、branch push と tag push の CI は両方通過しました。ソース候補では CPython 3.12.13、3.13.15、3.14.7 のローカル unit test suite、Ruff、任意の Parquet、100,000 observation の resource gate、1,000,000 trace の stress run、再現可能な wheel/sdist build、新しい環境への wheel install、公開ファイル検査、lock した依存関係の監査が通過しました。ローカル Langfuse 4.56.0 と 4.70.0 に対する架空の REST pull は、モデル呼び出しなしで offline import と一致しました。その後の `main` の commit は tag 済み release を変えません。package index への公開は行っていません。

## v1 のリリース規則

- ソースデータ、capture、export は既定でローカルかつ非公開です。通常の CLI report に prompt、tool 結果、credential、元の provider payload を出してはいけません。
- すべての変換は、ソース ID・hash、schema/adapter version、filter rule、正確な除外条件を記録します。検証可能な収集根拠のない `complete` 宣言は、完全な episode の証拠として扱いません。
- `faithful` は必要な capture 関係を観測したことだけを意味します。`eligible_for_<view>` はさらに view の形式と申告された用途を確認します。回答の正しさは別の label です。
- 不明または曖昧な関係は忠実な学習用 view から除外し、診断には残します。
- provider/model の利用規約と第三者 corpus の権利は Trailgate の利用者が調査・申告します。library は申告を強制できますが、法的許可を認証できません。
