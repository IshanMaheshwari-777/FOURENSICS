# FOURENSICS development exploration — saved real outputs

Small sample: treat these numbers as indicative, not as a benchmark.

| Mode | n | Coverage | Accuracy (answered) | Accuracy (all) | Macro-F1 |
| --- | --- | --- | --- | --- | --- |
| strict | 9 | 33% | 33% | 11% | 0.11 |
| forced | 9 | 100% | 22% | 22% | 0.19 |
| baseline_partial | 9 | 33% | 33% | 11% | 0.10 |
| revalidated_cached | 9 | 22% | 50% | 11% | 0.11 |

## strict: confusion matrix (rows = reference label)

| reference \ predicted | confirmed | invalid | cannot_reproduce | abstain |
| --- | --- | --- | --- | --- |
| confirmed | 0 | 1 | 0 | 0 |
| invalid | 0 | 1 | 0 | 3 |
| cannot_reproduce | 1 | 0 | 0 | 3 |

| class | precision | recall | support |
| --- | --- | --- | --- |
| confirmed | 0% | 0% | 1 |
| invalid | 50% | 25% | 4 |
| cannot_reproduce | n/a | 0% | 4 |

## forced: confusion matrix (rows = reference label)

| reference \ predicted | confirmed | invalid | cannot_reproduce | abstain |
| --- | --- | --- | --- | --- |
| confirmed | 0 | 1 | 0 | 0 |
| invalid | 2 | 2 | 0 | 0 |
| cannot_reproduce | 4 | 0 | 0 | 0 |

| class | precision | recall | support |
| --- | --- | --- | --- |
| confirmed | 0% | 0% | 1 |
| invalid | 67% | 50% | 4 |
| cannot_reproduce | n/a | 0% | 4 |

## baseline_partial: confusion matrix (rows = reference label)

| reference \ predicted | confirmed | invalid | cannot_reproduce | abstain |
| --- | --- | --- | --- | --- |
| confirmed | 0 | 1 | 0 | 0 |
| invalid | 0 | 1 | 0 | 3 |
| cannot_reproduce | 0 | 1 | 0 | 3 |

| class | precision | recall | support |
| --- | --- | --- | --- |
| confirmed | n/a | 0% | 1 |
| invalid | 33% | 25% | 4 |
| cannot_reproduce | n/a | 0% | 4 |

## revalidated_cached: confusion matrix (rows = reference label)

| reference \ predicted | confirmed | invalid | cannot_reproduce | abstain |
| --- | --- | --- | --- | --- |
| confirmed | 0 | 1 | 0 | 0 |
| invalid | 0 | 1 | 0 | 3 |
| cannot_reproduce | 0 | 0 | 0 | 4 |

| class | precision | recall | support |
| --- | --- | --- | --- |
| confirmed | n/a | 0% | 1 |
| invalid | 50% | 25% | 4 |
| cannot_reproduce | n/a | 0% | 4 |

## Interpretation and limits

- Nine of thirteen dev cases were analysed; the held-out split was not run.
- Live inference reached its 200,000-token daily quota during the baseline. Missing baseline answers are unavailable, not wrong model predictions.
- This exploratory batch ran while code/prompts were being developed; its original pipeline field is 16. It is not a frozen evaluation of the final implementation.
- `revalidated_cached` replays current source probes and citation validation on old model verdicts. It makes no model calls and is not a full current-pipeline accuracy result.
- The table-selection false confirmation used unrelated chart code. Preserving similarity provenance removes its directional support in the replay.
- A confirmed documentation request is mapped to invalid by the original issue-kind mapping; this exposes a mismatch between closure-label semantics and defect triage.
- Repository and label are confounded; the confirmed class is tiny. No broad accuracy or competitor-superiority claim is justified.
- Baseline coverage is incomplete, so these rows do not establish that either approach is better.

## Case audit

| Case | Reference proxy | Strict | Forced | Cached baseline | Revalidated old verdict |
| --- | --- | --- | --- | --- | --- |
| [hiyouga/LlamaFactory#8570](https://github.com/hiyouga/LlamaFactory/issues/8570) | invalid | abstain | confirmed | invalid | abstain |
| [marimo-team/marimo#7986](https://github.com/marimo-team/marimo/issues/7986) | cannot_reproduce | abstain | confirmed | unavailable | abstain |
| [python-semantic-release/python-semantic-release#1303](https://github.com/python-semantic-release/python-semantic-release/issues/1303) | confirmed | invalid | invalid | invalid | invalid |
| [hiyouga/LlamaFactory#8949](https://github.com/hiyouga/LlamaFactory/issues/8949) | invalid | invalid | invalid | unavailable | invalid |
| [marimo-team/marimo#6415](https://github.com/marimo-team/marimo/issues/6415) | cannot_reproduce | abstain | confirmed | invalid | abstain |
| [hiyouga/LlamaFactory#8991](https://github.com/hiyouga/LlamaFactory/issues/8991) | invalid | abstain | invalid | unavailable | abstain |
| [marimo-team/marimo#5743](https://github.com/marimo-team/marimo/issues/5743) | cannot_reproduce | confirmed | confirmed | unavailable | abstain |
| [hiyouga/LlamaFactory#8725](https://github.com/hiyouga/LlamaFactory/issues/8725) | invalid | abstain | confirmed | unavailable | abstain |
| [marimo-team/marimo#7883](https://github.com/marimo-team/marimo/issues/7883) | cannot_reproduce | abstain | confirmed | unavailable | abstain |

### hiyouga/LlamaFactory#8570

how to infer Qwen2-VL?

Primary finding: inconclusive. The evidence only contains literal text matches of the words ‘image_grid_thw’ and ‘images’ in documentation and source files; none of the items show the runtime check that would raise the ValueError. Therefore the claim cannot be confirmed or refuted based on the available static evidence.

### marimo-team/marimo#7986

Upgrading to Marimo 0.19+ breaks marimo edit

Primary finding: inconclusive. The code in marimo/_cli/run_docker.py constructs a docker command that uses the uv image, but there is no evidence in the static check that the command fails or that the failure is due to a missing Docker binary or permission issue. The static analysis only shows the command is built, not its runtime result.

### python-semantic-release/python-semantic-release#1303

docs: improve section about GitHub Actions

Primary finding: inconclusive. The evidence found doesn't settle this claim either way.

Validator: only documentation backs this verdict; documentation shows intent, not what the code does.

### hiyouga/LlamaFactory#8949

数据 history

Primary finding: inconclusive. No evidence directly shows whether sharegpt format data is concatenated into the model input. The provided items only contain literal matches of the word "messages" or related terms, which do not reveal runtime behavior.

### marimo-team/marimo#6415

molab.Marimo notebook went wrong in firefox

Primary finding: inconclusive. None of the provided evidence items show the creation of a notebook or workspace, nor do they indicate a failure or error message. The items are either unrelated code snippets, tests, or documentation that do not cover the notebook/workspace creation flow.

### hiyouga/LlamaFactory#8991

cannot import name 'pil_torch_interpolation_mapping' from 'transformers.image_utils'

Primary finding: inconclusive. The evidence found doesn't settle this claim either way.

Validator: no cited evidence contradicts the claim (absence alone does not count).

### marimo-team/marimo#5743

mo.ui.table filter clears on select all

Primary finding: supported. The `_convert_value` method assigns the selection and then calls `_filter_dataframe`. When the selection is empty (`not value or not flat`), `_convert_value` returns an empty DataFrame, effectively clearing any previous selection. The `apply_selection` method also calls `_filter_dataframe` with the stored selections, so applying a filter that results in no selection clears the selection state.

### hiyouga/LlamaFactory#8725

在帮助文档中，[--pref_loss {sigmoid,hinge,ipo,kto_pair,orpo,simpo}]，但是，报错ValueError: Unknown loss type: kto_pair. Should be one of ['sigmoid', 'hinge', 'ipo', 'bco_pair', 'sppo_hard', 'nca_pair', 'robust', 'exo_pair']，还没更新吗？

Primary finding: inconclusive. 

### marimo-team/marimo#7883

Cannot read properties of undefined (reading 'channel')

Primary finding: inconclusive. 
