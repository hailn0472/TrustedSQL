# TrustedSQL Experiment Design

Tai lieu nay mo ta ba thiet ke thi nghiem chinh dung cho paper TrustedSQL. Tat ca thi nghiem su dung cung benchmark layout, cung metric tu dong va khong dung human review/adjudication trong metric chinh.

## Common Protocol

### Dataset

Tat ca thi nghiem chinh chay tren evaluation benchmark v3:

`data/benchmark/v3/full/`

| Dataset split | File | Sequences | Turns |
|---|---|---:|---:|
| Benign single-turn | `SingleTurn_Benign_records.json` | 600 | 600 |
| Benign multi-turn | `Multiturn_Benign_records.json` | 140 | 573 |
| RBAC single-turn attacks | `SingleTurn_RBAC_Violation_records.json` | 400 | 400 |
| Prompt-injection single-turn attacks | `SingleTurn_PromptInjection_Malicious_records.json` | 318 | 318 |
| Malicious multi-turn attacks | `Multiturn_Malicious_records.json` | 420 | 1,728 |

Trong tai lieu nay:

- Evaluation benchmark la dataset dung de bao cao EX1, EX2 va EX3.
- Smoke-test subset chi la cach chay nho de kiem tra pipeline, khong phai dataset ket qua paper.
- GNN model-development corpus la dataset rieng dung de train, chon checkpoint va danh gia noi bo M2. Ban train/validation/test dang hoat dong nam trong `data/training/intent_gnn/v1/`; cac ban lich su, hard/locked diagnostics va ho so review nam trong supplementary artifact package.
- Human-review records la ho so audit dataset, neu cong bo thi nen di kem thesis artifact package.

Smoke test/debugging nen dung `--max-samples` tren evaluation benchmark profile truoc khi chay full. Ket qua paper khong lay tu smoke run neu khong duoc ghi ro la pilot.

### Runtime and Evaluation Rules

- Runtime modules khong duoc dung `sql_gt`, expected result, dataset labels, attack tags, human review labels hoac evaluator evidence.
- Evaluation duoc tach thanh hai phase: `runtime` va `evaluate`.
- `ERROR` khong bao gio duoc tinh la tu choi dung.
- Khong dung cache khi chay ket qua chinh.
- Cung mot thi nghiem phai giu co dinh code, dataset, schema, policy, role matrix, prompt va provider config.
- Neu chay tren nhieu may, run config snapshot cua runtime la source of truth cho model/config da chay.

### Metrics

Moi setting trong cac thi nghiem duoc tinh cung bo metric:

Utility:

- `ST-EX`
- `ST-Soft-F1`
- `MT-Turn-EX`
- `MT-Turn-Soft-F1`
- `MT-IEX`

Single-turn security, tinh rieng cho RBAC va PI:

- `ASR`
- `Refusal Recall`
- `Refusal Precision`
- `Refusal F1`
- `Over-refusal Rate`

Multi-turn security:

- `Prefix-RS`
- `Sequence ASR`
- `Sequence Refusal Recall`
- `Conditional ASR`
- `Conditional Refusal Recall`
- `Valid Secure Sequence Rate`

Performance:

- Benign served path
- RBAC blocked path
- PI blocked path
- Multi-turn secure sequence path

Performance bao gom count, mean latency, p95 latency, input/output tokens per unit va total input/output tokens.

## EX1 - Main Full-Result Tables

### Muc tieu

EX1 tra loi cau hoi: TrustedSQL full method hoat dong nhu the nao tren evaluation benchmark v3 voi cac model khac nhau?

Day la thi nghiem ket qua chinh cua method. No cho thay utility, security va performance cua pipeline hoan chinh, dong thoi cho phep so sanh tac dong cua model backend.

### Dataset

Chay tren evaluation benchmark v3:

`data/benchmark/v3/full/`

Dung toan bo nam split:

- benign single-turn
- benign multi-turn
- RBAC single-turn attacks
- prompt-injection single-turn attacks
- malicious multi-turn attacks

### Settings

Chi chay mot setting:

| Setting id | Label | Module path |
|---|---|---|
| `full_trustedsql` | Full TrustedSQL | `C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1` |

### Models

EX1 chay tren ba model/provider backend:

| Model group | Intended use |
|---|---|
| Gemini 2.5 Flash | Default model, ket qua chinh neu can chon mot cau hinh mac dinh |
| GPT-OSS-20B | Open-weight/smaller backend comparison |
| GPT-OSS-120B | Larger open-weight backend comparison |

Moi model duoc chay 1 lan tren evaluation benchmark v3 voi cung setting `full_trustedsql`.

### Number of Runs

| Model | Runs | Dataset |
|---|---:|---|
| Gemini 2.5 Flash | 1 | evaluation benchmark v3 |
| GPT-OSS-20B | 1 | evaluation benchmark v3 |
| GPT-OSS-120B | 1 | evaluation benchmark v3 |

Tong cong: 3 runtime runs.

### Output Tables

EX1 nen sinh cac bang paper chinh:

1. Utility
2. RBAC Single-Turn Security
3. Prompt-Injection Single-Turn Security
4. Multi-Turn Security
5. Performance, tach theo runtime path

### Nhan dinh co the rut ra

EX1 dung de phan tich:

- Model nao giu utility tot hon tren benign single-turn va multi-turn.
- Model nao co attack success rate thap hon tren RBAC/PI.
- Model nao co Prefix-RS va Valid Secure Sequence Rate tot hon tren malicious multi-turn.
- Trade-off giua utility, refusal behavior va over-refusal.
- Chi phi token/latency cua tung model trong cung pipeline.

## EX2 - Baseline Comparison

### Muc tieu

EX2 tra loi cau hoi: TrustedSQL full method khac gi so voi generator-only va architecture baseline cu khi cung chay tren mot benchmark, mot metric va mot model?

Thi nghiem nay khong nen trinh bay nhu "defense-in-depth vs non-defense-in-depth", vi architecture cu cung co nhieu lop phong ve. Nen trinh bay no nhu so sanh method:

- generator-only baseline
- previous/reference architecture baseline
- proposed TrustedSQL method

### Dataset

Chay tren evaluation benchmark v3:

`data/benchmark/v3/full/`

Dung toan bo nam split giong EX1.

### Model

Dung Gemini 2.5 Flash cho tat ca baseline.

Ly do:

- Day la default model cho pipeline hien tai.
- Giam chi phi so voi chay baseline tren ca 20B/120B.
- Giu bien model co dinh de so sanh kien truc/cong nghe xu ly.

### Settings

| System | Setting id | Module path | Source |
|---|---|---|---|
| Generator-only baseline | `generator_only_control` | `C0 -> G1 -> X1` | `architecture_baselines` |
| Previous architecture baseline | `full_architecture` | `C0 -> D1 -> D2 -> G1 -> D3 -> D4 -> X1` | `architecture_baselines` |
| Proposed method | `full_trustedsql` | `C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1` | `src/trustedsql/` |

### Number of Runs

Neu da co EX1 Gemini 2.5 Flash cho `full_trustedsql`, co the reuse run do.

| Setting | Runs needed | Dataset |
|---|---:|---|
| `generator_only_control` | 1 | evaluation benchmark v3 |
| `full_architecture` | 1 | evaluation benchmark v3 |
| `full_trustedsql` | 0 or 1 | reuse EX1 Flash neu cung code/config |

Tong cong: 2 run moi neu reuse EX1, hoac 3 run neu khong reuse.

### Output Tables

EX2 dung cung 5 nhom bang metric nhu EX1:

1. Utility
2. RBAC Single-Turn Security
3. Prompt-Injection Single-Turn Security
4. Multi-Turn Security
5. Performance

### Nhan dinh co the rut ra

EX2 dung de phan tich:

- Generator-only dat utility/security ra sao khi khong co security modules.
- Previous architecture baseline trade off utility va security nhu the nao.
- TrustedSQL co cai thien multi-turn robustness, refusal behavior, over-refusal hoac utility so voi baseline nao.
- Diem manh/yếu cua method moi khong bi nham lan voi tac dong cua model, vi model duoc giu co dinh.

## EX3 - Ablation Study

### Muc tieu

EX3 tra loi cau hoi: tung block/module cua TrustedSQL dong gop gi vao utility va security?

Thay vi tao mot EX4 parameter sweep rieng, ablation study la cach gon va khoa hoc hon trong boi canh hien tai, vi metric chinh do kha nang `DENY` attack va phuc vu benign request. Cac bien audit-only khong lam thay doi runtime decision se khong phu hop voi metric hien tai.

### Dataset

Chay tren evaluation benchmark v3:

`data/benchmark/v3/full/`

Khong dung smoke-test subset lam ket qua chinh. Smoke run chi dung de kiem tra setting moi truoc khi chay full.

### Model

Dung Gemini 2.5 Flash cho tat ca ablation settings.

Ly do:

- La default model.
- Da duoc dung trong EX1/EX2.
- Giu model co dinh de do tac dong cua module/block thay vi tac dong cua backend.

### Core Settings

| Setting | Module path | Question answered |
|---|---|---|
| Full TrustedSQL | `C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1` | Muc chuan cua method |
| Full - M1 | `C0 -> M2 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1` | Prompt Integrity Guard dong gop bao nhieu vao PI/RBAC/multi-turn security va over-refusal |
| Full - M2 | `C0 -> M1 -> M3 -> M4 -> M5 -> M6 -> M7 -> X1` | Conversational/GNN Risk Guard dong gop bao nhieu vao multi-turn attack refusal |
| Full - M3/M4/M5 | `C0 -> M1 -> M2 -> M6 -> M7 -> X1` | Policy-grounded access planning, table/column validation input contract, va row-scope proof block dong gop bao nhieu |
| Full - M7 | `C0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6 -> X1` | SQL Conformance Validator dong gop bao nhieu sau SQL generation |

### Why Ablate M3/M4/M5 as One Block

Khong nen bo rieng `M3`, `M4` hoac `M5` trong core ablation vi ba module nay phu thuoc nhau ve contract:

- `M3` lap resource/access plan.
- `M4` validate plan va tao resource contract.
- `M5` dung contract de proof row scope.

Neu bo rieng mot module, pipeline se can fallback hoac contract nhan tao. Dieu nay lam ket qua kem sach ve mat khoa hoc. Vi vay `Full - M3/M4/M5` do contribution cua ca policy-grounded access block mot cach ro rang hon.

### Optional M1 Operating Mode Settings

Neu can phan tich sau hon ve M1, co the them hai setting phu:

| Setting | Description | Purpose |
|---|---|---|
| M1 rule-only | M1 chi dung rule/heuristic, khong dung LLM classifier | Do rule-based PI guard co du khong |
| M1 LLM-only | M1 chi dung LLM classifier, tat rule/heuristic neu code ho tro sach | Do LLM classifier dong gop gi |

Hai setting nay khong bat buoc. Neu ngan sach han che, core settings da du cho ablation study.

### Number of Runs

Neu EX1 Flash Full TrustedSQL da ton tai va cung code/config, reuse lam Full baseline.

Core EX3:

| Setting | Runs needed | Dataset |
|---|---:|---|
| Full TrustedSQL | 0 or 1 | reuse EX1 Flash neu cung code/config |
| Full - M1 | 1 | evaluation benchmark v3 |
| Full - M2 | 1 | evaluation benchmark v3 |
| Full - M3/M4/M5 | 1 | evaluation benchmark v3 |
| Full - M7 | 1 | evaluation benchmark v3 |

Tong cong: 4 run moi neu reuse EX1, hoac 5 run neu khong reuse.

Extended EX3 voi M1 modes:

| Additional setting | Runs needed | Dataset |
|---|---:|---|
| M1 rule-only | 1 | evaluation benchmark v3 |
| M1 LLM-only | 1 | evaluation benchmark v3 |

Tong cong extended: 6 run moi neu reuse EX1, hoac 7 run neu khong reuse.

### Output Tables

EX3 dung cung bo metric voi EX1/EX2. Ngoai bang absolute metrics, nen them bang delta so voi Full TrustedSQL:

| Setting | Delta ST-EX | Delta MT-IEX | Delta RBAC ASR | Delta PI ASR | Delta Sequence ASR | Delta Valid Secure Sequence Rate |
|---|---:|---:|---:|---:|---:|---:|

Delta giup thay ro khi bo mot module/block thi utility/security thay doi theo huong nao.

### Nhan dinh co the rut ra

EX3 dung de phan tich:

- `Full - M1`: neu PI ASR tang hoac Refusal Recall giam, M1 co dong gop vao prompt-injection defense. Neu Over-refusal Rate giam/tang, co the phan tich cost cua M1 len benign utility.
- `Full - M2`: neu Sequence ASR tang hoac Valid Secure Sequence Rate giam, M2 co dong gop vao multi-turn attack refusal.
- `Full - M3/M4/M5`: neu RBAC ASR tang manh, block policy-grounded access/proof la thanh phan chinh cho RBAC/RLS enforcement. Neu utility tang khi bo block, do la trade-off giua row-scope proof va SQL accuracy.
- `Full - M7`: neu ASR tang sau khi bo M7, SQL validator co vai tro chan SQL sinh ra sai policy sau generation. Neu utility tang, can thao luan ve conservative validator trade-off.

### Suggested Graphs

EX3 co the tao cac bieu do sau:

1. Security-Utility scatter:
   - x-axis: `ST-EX`
   - y-axis: `RBAC Refusal Recall` hoac `PI Refusal Recall`
   - color: `Over-refusal Rate`

2. Multi-turn scatter:
   - x-axis: `Prefix-RS`
   - y-axis: `Sequence Refusal Recall`
   - size: `Valid Secure Sequence Rate`

3. Ablation delta bar chart:
   - x-axis: ablation setting
   - y-axis: delta vs Full TrustedSQL
   - metrics: `ST-EX`, `MT-IEX`, `RBAC ASR`, `PI ASR`, `Sequence ASR`, `Valid Secure Sequence Rate`

4. Optional M1 mode chart:
   - compare `Full`, `M1 rule-only`, `M1 LLM-only`, `Full - M1`
   - metrics: `PI ASR`, `PI Refusal Recall`, `Over-refusal Rate`, `ST-EX`

## Final Recommended Experiment Set

Neu muon bo thi nghiem gon nhung du manh cho paper:

| Experiment | Model(s) | Dataset | Runs if reusing EX1 Flash |
|---|---|---|---:|
| EX1 Main Full-Result Tables | Gemini 2.5 Flash, GPT-OSS-20B, GPT-OSS-120B | evaluation benchmark v3 | 3 |
| EX2 Baseline Comparison | Gemini 2.5 Flash | evaluation benchmark v3 | 2 new runs |
| EX3 Ablation Study | Gemini 2.5 Flash | evaluation benchmark v3 | 4 new runs |

Tong toi thieu: 9 runtime runs, trong do EX1 Flash duoc reuse cho EX2 va EX3.

Neu them M1 rule-only va M1 LLM-only: 11 runtime runs.

## Execution Commands

Primary workflow:

```powershell
python evaluation/run_experiment.py --experiment configs/experiments/ex1_main_full_results.yaml --phase all
python evaluation/run_experiment.py --experiment configs/experiments/ex2_baseline_comparison.yaml --phase all
python evaluation/run_experiment.py --experiment configs/experiments/ex3_ablation.yaml --phase all
```

Partial workflow:

```powershell
python evaluation/run_experiment.py --experiment configs/experiments/ex3_ablation.yaml --phase runtime
python evaluation/run_experiment.py --experiment configs/experiments/ex3_ablation.yaml --phase evaluate
python evaluation/run_experiment.py --experiment configs/experiments/ex3_ablation.yaml --systems trustedsql_minus_m2 --phase all
```

Moi run duoc materialize thanh resolved config rieng trong `outputs/experiments/<experiment_run_id>/resolved_configs/`, sau do runtime artifacts nam trong `outputs/runs/<run_id>/`.

## Notes for Paper Writing

- Khong nen goi EX2 la proof cua "defense-in-depth", vi old architecture cung la multi-module defensive architecture.
- Nen goi EX2 la baseline comparison giua proposed TrustedSQL method va reference systems.
- Nen goi EX3 la component/block ablation.
- Khong can EX4 rieng neu no chi lap lai ablation voi bien audit-only. Audit-only setting khong phu hop voi metric security hien tai vi metric do attack co bi `DENY` hay khong.
- Performance khong can la mot experiment rieng. No nen duoc report trong EX1/EX2/EX3 theo cung runtime outputs.
