# Provenance профиля Affective Resonance

Статус: **Production capability**

Реализовано в PR #10 и присутствует в `main`, начиная с merge commit `49c771f4edff140a96e505f4a96a31ccf61a87ef`.

## Наблюдаемый контракт

Успешное вычисление affective resonance возвращает bridge с полем:

```json
{
  "profile_source": "explicit"
}
```

`profile_source` сообщает, откуда был получен resonance profile.

| Значение | Режим | Смысл |
|---|---|---|
| `explicit` | LIVE | Профиль получен из текущего environment. |
| `history` | LIVE | Использован последний подходящий durable event `resonance_profile_computed`. |
| `neutral_fallback` | LIVE | Explicit и подходящий historical profile отсутствуют, поэтому использован существующий neutral fallback. |
| `legacy_unknown` | Только REPLAY | Legacy event `affective_resonance_applied` не содержит сохранённого `profile_source`; исходный источник невозможно безопасно восстановить. |

LIVE никогда не создаёт и не сохраняет `legacy_unknown`.

## Durable ownership

Для новых событий `affective_resonance_applied` каноническим durable-владельцем является:

```python
event["profile_source"]
```

Возвращаемый bridge проецирует это значение runtime consumer:

```python
final_bridge["profile_source"]
```

Persisted bridge намеренно не дублирует ownership:

```python
"profile_source" not in event["bridge"]
```

Существующее поле события не изменено:

```python
event["source"] == "resonate_with_user"
```

`source` обозначает операцию, создавшую событие, и не является provenance профиля.

## Порядок resolution в LIVE

Runtime разрешает профиль один раз до вычисления deltas:

```text
profile текущего environment
→ последний подходящий durable history profile
→ neutral fallback
```

Затем разрешённый профиль передаётся в существующее вычисление affective deltas. Mirror, regulate, dampen, rounding, clamp boundaries, event order и atomic PAD mutation не изменены добавлением provenance.

## Поведение replay

Для нового события с валидным сохранённым source replay:

1. потребляет сохранённый `affective_resonance_applied`;
2. валидирует `event["profile_source"]`;
3. применяет записанные deltas;
4. возвращает сохранённый provenance в derived bridge;
5. не вызывает profile resolver.

Для legacy event без ключа replay возвращает `legacy_unknown` только в derived bridge. Поле не добавляется в historical event, persisted bridge или execution history; старые hashes не пересчитываются.

Если ключ присутствует, но имеет некорректное значение, replay завершается fail-closed с replay history mismatch. Повреждённое значение не заменяется fallback provenance.

## Duplicate application

Если событие уже применено, runtime не применяет PAD deltas повторно, не добавляет новый affective tag и не вызывает resolver. Возвращается копия bridge с валидированным или derived `profile_source`; event и его persisted bridge остаются неизменными.

## Граница целостности

`profile_source` является частью top-level event payload. Поэтому существующая hash chain по полному payload покрывает provenance новых событий. Для этой возможности не изменялись hash algorithm, canonical serialization, history seed, checkpoint format, snapshot format и CVM ABI.

## Совместимость

- Новый runtime + старая history: поддерживается через derived projection `legacy_unknown`.
- Старый runtime + новое событие: pre-P1 apply path принимает аддитивное top-level поле и продолжает применять сохранённые deltas.
- Legacy events остаются неизменными.
- Существующие history hashes не пересчитываются.
