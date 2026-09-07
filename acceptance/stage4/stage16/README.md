# Stage 16: внешняя тестовая платформа

Стенд запускает существующие Baseline и Gold, сохраняет полный план эксперимента,
повторно открывает физические доказательства и проверяет устойчивость защит к
заранее заданным мутациям. Это сопровождаемый инструмент приёмки.

Практическая цель — видеть, решена ли задача, сколько попыток и ресурсов она
потребовала и как меняется результат при повторении. Продукт уже выполняет
задачу и собирает исходные измерения; стенд организует повторные испытания и
проверяет их отчёты. Дополнительного контрольного Gold или отдельного runtime нет.

По согласованному изменению плана Stage 16 экспериментальный диспетчер находится
в `acceptance/`, а не в продуктовом `gold/paired.py`. Продуктовый Gold запускается
через `python -m synapse → synapse.cli.main`. Baseline использует существующий
`run_baseline_task`. Его единственное расширение — необязательный порт учёта в
существующем вызове worker. C2 evaluator и writer Stage 3A остаются прежними.

## Что означает результат

`DIAGNOSTIC_COMPLETE` означает, что оба фактических запуска закончились, их
измерения и источники доступны, а параметры диагностической пары совпали. Это ещё не
контрфактическое доказательство экономии от reuse. Старый Baseline и Gold имеют
различные execution policies: Gold дополнительно проходит accepted plan и
Controlled Change. Отчёт явно возвращает `EXECUTION_POLICIES_DIFFER` и запрещает
экономический claim. C2 получает настоящий `GOLD_WITH_CARRY` и сохраняет свой
`INVALID_GOLD_WITH_CARRY`; переименование arm ради принятия C2 запрещено.
Все проверенные оси, включая различие execution policies, присутствуют в
`parameter_alignment`; `execution_policies_comparable` остаётся `false` при
любом несовпадении. Полнота диагностики не объявляет эти политики одинаковыми.

В отчёте v2 `runs` содержит строку для каждого запланированного запуска:
состояние исполнения, исходный outcome и его ссылку, число попыток, токены,
известную стоимость, длительность и статус измерений. `task_results` группирует
повторения по задаче и arm: число запрошенных/завершённых запусков, решённых,
нерешённых и неизвестных исходов, среднее, разброс и диапазон измерений.
Неисполненная реплика остаётся в знаменателе; несовпадающие параметры реплик
блокируют их общее среднее. Исходные attempts, calls и reconciliation reports
сохранены в подробных данных.

Статус задачи и полнота измерений независимы. Например, `FULL` с потерянным
resource report остаётся исходным подтверждённым `FULL`, а учёт получает
`INCOMPLETE`. Отсутствующее доказательство самого результата не даёт нового
подтверждения успешности. Зелёное завершение процесса не заменяет эти поля.

Разность считается как **Baseline минус Gold**. Токены, внешняя длительность,
деньги и инфраструктурные измерения приведены отдельно. Отрицательные разности
сохраняются. Пропущенный запуск остаётся в выборке с неизвестным значением;
среднее неполной выборки не вычисляется. Для полной выборки публикуются размер,
среднее, выборочное стандартное отклонение и диапазон. Единица наблюдения —
парный запуск; принадлежность задаче сохраняется. Доверительные интервалы и
выводы о генеральной совокупности этот диагностический профиль не строит.
Параметры реплик одной задачи тоже должны совпадать; другой base/model/budget
не маскируется номером реплики. Отчёт отдельно фиксирует код и среду текущего
evaluator, сохраняя прежние execution identities из preregistration.

Provider cache виден в исходных `LLMCallRecord` и не создаёт semantic reuse.
Activation проверяется отдельно по Stage 14/15: публикация производителя,
retrieval, snapshot, replay, admission перед consumption, context, worker result,
MechanismUseRecord, promotion, verification и outcome. Ссылки на outcome попытки
производителя и на итог всего его запуска различаются; они не переписываются.
Только полная сравнимая диагностическая пара с такой цепочкой учитывается в
пороге activation. Недостаточный порог даёт
`MECHANISM_NOT_ACTIVATED / INCONCLUSIVE`.

Infrastructure reports сохраняют исходные ограничения Stage 15: CPU относится
к измеренным потокам владельца, дочерние процессы исключены; I/O относится к
измеренным примитивам persistence, а не ко всему диску; интервалы разных buckets
нельзя механически складывать. Внешняя длительность — сумма наблюдавшихся
вызовов стенда, без ожидания оператора между ними. Цены не выдумываются;
отсутствующая стоимость остаётся неизвестной. Учёт Baseline не превращает его
неизмеренные файловые операции в измеренный Gold I/O.

## Подготовка и запуск

Установите зависимости из зафиксированного профиля репозитория:

```bash
python -m pip install -e '.[gold-worker]' pytest
```

Для каждого arm каждой реплики нужны отдельные чистые Git repositories, run
directories и, для Gold, отдельный project state. Используйте независимые clones,
например `git clone --no-local`; общие worktrees и Git alternates не допускаются.
Run directory до запуска не существует. Данные и результаты эксперимента храните
вне рабочего checkout кода. Сохраните исходные stores вместе с экспериментом.
Это разделение экспериментальных данных; оно не создаёт security sandbox
для процессов worker и не изолирует общую нагрузку хоста.

Gold definition указывает на обычные, уже подготовленные входы продукта:

```json
{
  "arm": "GOLD",
  "repo_root": "/experiments/r0/gold/repo",
  "run_root": "/experiments/r0/gold/run",
  "state_root": "/experiments/r0/gold/project",
  "declaration_ref": {
    "path": "/experiments/r0/gold/input.json",
    "sha256": "<SHA-256 фактических байтов input.json>",
    "bytes": 1234
  }
}
```

`declaration_ref` создаётся функцией `source(path)` из `protocol.py`;
`bytes` и hash выше — обозначения, их нужно заменить фактическими значениями.
Gold input использует существующую схему v2 и pinned worker accounting profile.
`cli_timeout_seconds` в definition необязателен; по умолчанию 600 секунд.

Baseline definition содержит существующие контракты без нового исполнения:

| Поле | Содержание |
| --- | --- |
| `arm` | `BASELINE` |
| `repo_root`, `run_root` | Абсолютные независимые пути |
| `base_revision`, `max_attempts` | Точный base и лимит попыток |
| `task` | Поля `BaselineTask`: task_id, instance_id, statement, allowed_scope |
| `mini` | Поля существующего `MiniInvocationConfig` |
| `oracle` | Поля `SWEbenchHarnessOracleConfig`, со своим swebench_work_dir |
| `provider_connection` | model берётся из mini; здесь credential_env, endpoint, timeout_seconds |

Credentials передаются через указанную переменную окружения. Значение секрета
не входит в протокол. Runtime SDK identities берутся из реального worker profile.
Host profile измеряется непосредственно и содержит только явно перечисленные
несекретные настройки; contention и provider cache не объявляются управляемыми.

Для Gemini существующий Mini transport принимает официальный endpoint
`https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` и имя
модели без routing prefix, например `gemini-3.1-flash-lite`. Credential environment
может быть `GEMINI_API_KEY`. В capture сохраняются provider `gemini` и профиль
`gemini-openai-chat-usage/v1`; OpenAI-совместимый wire format не меняет identity
провайдера. Prompt/completion totals не складываются повторно с cache/thinking.
Если Gemini не прислал подробности subsets, они остаются неизвестными. Mini
использует Gemini rates из pinned LiteLLM для своего оперативного лимита;
эта оценка не становится подтверждённой стоимостью в отчёте Stage 15/16.
См. [официальный совместимый API](https://ai.google.dev/gemini-api/docs/openai).
Ответ, который Mini отклонил из-за неверного tool format, также учитывается:
SDK сохраняет его внутри сообщения `FormatError`. Разбор этих сообщений
принадлежит существующему Mini adapter; aggregate, reconciliation и внешняя
проверка используют тот же разбор. Статус ошибки при этом сохраняется.

### Подготовка первого живого сценария

`astropy__astropy-12907` зафиксирован на base
`d16bfe05a744909de4b27f5875fe0d4ed41ce607`. Команда подготовки проверяет SHA-256
конкретной ревизии SWE-bench Verified, сохраняет исходную задачу отдельно от
evaluator data и создаёт чистый checkout. Данные эксперимента находятся снаружи
репозитория Synapse.

```bash
python -m pip install 'swebench==4.1.0' 'pyarrow==21.0.0'
python -m acceptance.stage4.stage16 prepare-astropy --root /experiments/astropy-01
python -m acceptance.stage4.stage16 calibrate-astropy --root /experiments/astropy-01
```

Вторая команда требует Docker. Она фиксирует фактический image ID и выполняет
существующий `SWEbenchHarnessOracleRunner` для двух калибровок: изменение только
комментария сохраняет ошибку, эталонный патч SWE-bench должен её исправить.
Проверяются реальные исходы и отсутствие инфраструктурной ошибки; сохраняются
candidate patches, reports и логи. Ответов модели в этой калибровке нет.
Workflow `Astropy Pilot Preparation` выполняет её отдельно от обычных unit tests.
Pin SWE-bench 4.1.0 соответствует существующему CLI-контракту oracle, включая
`env_image_tag`: в 3.0.15 этого аргумента ещё нет, а версия 5 изменила CLI.
Проверка использует существующий oracle adapter без дополнительных запускателей.

`CALIBRATED` подтверждает окружение oracle. Для живой пары ещё нужны обычные
Gold input и ранее допущенный, совместимый с задачей корпус с сохранёнными
источниками. Подготовка его не создаёт. Текущий `project connect` создаёт пустую
библиотеку; `RunKnowledge` отказывает при пустом corpus. Фикстурные
публикации и permissive probes в живом пилоте не используются. Четыре пары,
три попытки и seed 17 записаны как предложенные параметры; настоящий протокол
`freeze` создаётся только после готовности обоих arms. Эталонный патч и evaluator
каталоги не должны передаваться worker как рабочая область или контекст.

Корпус теперь можно получить через `project learn`; команда и ограничения
описаны в [инструкции Gold](../../../../docs/GOLD_KNOWLEDGE_INGESTION.md).
Сквозная приёмка наполнения не заменяет подготовку конкретной задачи и
проверку рабочего окружения модели.

Файл design перечисляет **все** задачи, реплики и оба arm заранее:

```json
{
  "experiment_id": "pilot-diagnostics-01",
  "seed": 17,
  "minimum_activated_pairs": 1,
  "specification": {
    "version": "Stage4-v2.2; external Stage16 acceptance profile",
    "path": "/experiments/governing-specification.docx"
  },
  "pairs": [{
    "pair_id": "task-a-r0",
    "task_id": "task-a",
    "replicate_id": 0,
    "inputs": {
      "BASELINE": "/experiments/r0/baseline-definition.json",
      "GOLD": "/experiments/r0/gold-definition.json"
    }
  }]
}
```

```bash
python -B -m acceptance.stage4.stage16 freeze --design /experiments/design.json --output /experiments/protocol.json
python -B -m acceptance.stage4.stage16 run --protocol /experiments/protocol.json --experiment /experiments/journal
python -B -m acceptance.stage4.stage16 run --experiment /experiments/journal --approve-pending
python -B -m acceptance.stage4.stage16 report --experiment /experiments/journal > /experiments/assessment.json
python -B -m acceptance.stage4.stage16 report --experiment /experiments/journal --format table
```

Таблица — краткое представление того же физически проверенного отчёта. Она
показывает задачу, реплику, arm, завершение, outcome, попытки, токены, секунды и
полноту учёта. JSON сохраняет подробные доказательства и ограничения измерений.

Freeze связывает actual specification bytes, code revision, product/verifier
source hashes, host profile, полный schedule, вложенные входы и начальные
repository/knowledge states. Порядок arms чередуется внутри каждой задачи,
начальная ориентация и порядок пар определяются seed. Для одной или нечётного
числа реплик идеальный баланс невозможен и не заявляется.

Без `--approve-pending` стенд останавливается на обычном запросе Gold approval
с exit code 3. Флаг выполняет стандартное действие оператора через canonical
CLI. Неудачный/прерванный arm даёт exit code 2; завершённый dispatch inventory — 0.
Команда `report` возвращает 0 при успешном построении отчёта, включая отчёт
`INCOMPLETE`; интерпретируйте его поля, а не код процесса как acceptance verdict.

## Восстановление и владение

SQLite journal использует WAL и `synchronous=FULL`. Durable STARTED предшествует
эффектам; OS lock исключает двух диспетчеров. История связывает protocol identity,
slot, порядок и предыдущий event hash. Проверка не является защитой от полного
злонамеренного переписывания trusted local operator: это существующая локальная
граница доверия проекта.

Повторная команда `run` продолжает прежний Gold через `project resume`.
У legacy Baseline нет durable resume: потеря процесса после STARTED оставляет
`INTERRUPTED`, неизвестный расход и сохранённые источники; повторные эффекты
не запускаются. Завершённые arms не исполняются снова. Подмена входов, начального
состояния, кода, среды, порядка или общей repository allocation блокирует запуск.
Если потеряна квитанция внешнего вызова, его длительность неизвестна:
`duration.total_ns = null`. Сумма сохранившихся квитанций остаётся отдельно в
`observed_subtotal_ns`. Восстановление Gold не превращает эту сумму в полное
время и не создаёт отсутствующее измерение. Такое сравнение получает `INCOMPLETE`.

Отчёт строится под тем же lock и перечитывает retained sources через существующие
reconciliation owners. Потеря файла блокирует новое полное сравнение, сохраняя
исторические receipts и domain outcomes. Standalone JSON не заменяет stores.

| Модуль | Ответственность |
| --- | --- |
| protocol.py | Неизменяемый дизайн и физические предусловия |
| harness.py | Durable scheduling, исключение одновременного dispatch и recovery |
| execution.py | Граница существующих Baseline API и Gold CLI |
| run_evidence.py | Перевод и повторная проверка физических доказательств arms |
| assessment.py | Внешняя диагностическая оценка полного набора |
| mutations.py | Изолированное ослабление, killer и restoration evidence |
| __main__.py | Операторский интерфейс внешнего стенда |

## Приёмка и мутации

Тяжёлые сценарии находятся в отдельных `test_*_acceptance.py` и отдельных CI
shards. Сохраняются предыдущие Stage 4 suites, C2 regression, dependency direction,
ownership и canonical entrypoint checks. Здесь добавлены actual paired execution,
полные реплики, реальные mismatches, два разных recovery contracts и activation
через реальные Mini/SDK requests с контролируемым HTTP provider и внешним oracle.
Отдельный сценарий `test_task_result_acceptance.py` исполняет реальное исправление
в обоих arms и проверяет независимый oracle, Gold `FULL`, расходы, табличный
отчёт и сохранение outcome при физической потере измерений.
Управляемые ответы проверяют архитектуру; они не измеряют качество коммерческой
модели и не заменяют технический пилот.

`mutations.json` задаёт шесть ослаблений Stage 16, включая потерянный интервал
времени, ошибочно учтённый как нулевой расход. Это добавление к прежней
adversarial suite, не заявление, что выполнена вся матрица Приложения F.
Каждый CI shard сначала запускает неизменённый killer, меняет один указанный
участок в изолированном checkout, требует ожидаемое падение и проверяет возврат
исходных bytes/code identity и чистого Git status. Ошибка окружения не считается
KILLED. JSON, JUnit и logs сохраняются как CI artifacts с revision и patch identity.

```bash
python -B -m acceptance.stage4.stage16.mutations --case S16-MUT-FINGERPRINT --output /experiments/mutant-fingerprint
```

Для mutation runner нужен чистый commit. Любой SURVIVED, неожиданная ошибка или
провал restoration делает соответствующий CI check красным. Зелёный стенд не
закрывает Stage 4: полная нормативная traceability, пилот, экономический дизайн
с достаточной причинной изоляцией и human acceptance требуют отдельных данных.

Дизайн блоков и парных разностей опирается на
[NIST: randomized block designs](https://www.itl.nist.gov/div898/handbook/pri/section3/pri332.htm)
и [NIST: paired observations](https://www.itl.nist.gov/div898/handbook/prc/section3/prc311.htm).
Граница durability следует
[SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) и
[SQLite transactions](https://www.sqlite.org/lang_transaction.html).


## Первичное знание из реальных источников

`project learn` реализован в каноничном продуктовом CLI. Порядок входов,
ограничения процедур и перенос результата в `knowledge_path` описаны в
[сопровождаемой инструкции](../../../../docs/GOLD_KNOWLEDGE_INGESTION.md).
Приёмочные файлы `test_source_*` разделены по публикации, процедурам, восстановлению,
удержанию, потреблению и отказу при изменённой среде; CI запускает их отдельно.
Они используют реальные stores и отдельные процессы, без заранее допущенного
тестового corpus. Детерминированный внешний worker проверяет доставку и не
служит результатом живого эксперимента.

Доставка source knowledge не удовлетворяет прежнему порогу activation,
который требует независимо подтверждённого предотвращения повторного C1 dispatch.
Отсутствующий `MechanismUseRecord` нельзя выдумывать для повышения оценки.
Новый профиль требует отдельно измерить практическую пользу полученного содержания.

Практическая последовательность испытания: сначала одна живая диагностическая
пара с проверенной средой worker, затем заранее зафиксированная серия.
Предлагаемая серия содержит две подготовительные задачи и четыре контрольные:
две на полезный перенос, одну со сменой условий и одну с нерелевантным опытом.
По три повторения двух режимов дают 24 запуска. Корпус для каждой контрольной
реплики берётся из одного исходного состояния; ответы контрольных задач не
попадают в другие реплики. Сценарии и критерии должны быть зафиксированы до
первого ответа модели. Это план выборки, а не сообщение, что эти задачи уже
подготовлены или выполнены. Текущий профиль точного commit ограничивает перенос
между версиями; сравнение с Baseline сохраняет различия execution policies.
