import type { DiagnosticCode } from "./connection/model";

export type Language = "en" | "ru";
export const copy = {
  en: {
    tagline: "A local workspace for governed AI",
    workspace: "Workspace",
    connection: "Connection",
    private: "Your materials stay local",
    project: "Project",
    noProject: "No project connected",
    projectHint:
      "Project access will become available with the project connection capability.",
    agents: "Agents",
    noAgents: "No active executions",
    agentsHint: "Execution is unavailable in this build.",
    eyebrow: "WORKSPACE CONNECTION",
    title: "A clear connection. A trusted starting point.",
    intro:
      "Synapse checks its local core before opening the workspace. Connection and execution permissions are kept separate.",
    native: "Preparing the desktop",
    connecting: "Connecting to the local core",
    ready: "Local core connected",
    fail: "Connection needs attention",
    stopping: "Stopping the local core",
    stopped: "Local core stopped",
    nativeHint: "The desktop shell will verify its bundled runtime.",
    connectingHint:
      "Verifying the runtime and agreeing on the control protocol.",
    readyHint:
      "The control connection is ready. Task execution and project access are not available yet.",
    stoppingHint: "Waiting for the core to confirm shutdown.",
    stoppedHint:
      "Shutdown was confirmed. Reopen the desktop to begin a new session.",
    retry: "Check connection again",
    stop: "Stop core",
    force: "Force close desktop",
    forceWarning:
      "Force close the desktop and its managed local processes? This does not undo external effects or establish unfinished outcomes.",
    desktop: "Desktop shell",
    core: "Local core",
    permissions: "Execution permissions",
    unavailable: "Unavailable",
    verified: "Connected",
    waiting: "Waiting",
    noGrant: "No execution authority",
    local: "Local pipes",
    network: "No UI-to-core network port",
    session: "Session details",
    version: "Runtime version",
    process: "Core process",
    profile: "Control profile",
    privacy: "Private by design",
    privacyHint:
      "Connecting to the local core does not send project files, memory, or conversations to a model.",
    diagnostics: "Connection diagnostics",
    noDiagnostics: "No diagnostic events.",
    reply: "Latest core reply",
    bottom: "Local control connection",
    footer: "Readiness is verified, never simulated.",
  },
  ru: {
    tagline: "Локальная среда для управляемого ИИ",
    workspace: "Рабочая среда",
    connection: "Подключение",
    private: "Ваши материалы остаются локально",
    project: "Проект",
    noProject: "Проект не подключён",
    projectHint:
      "Доступ к проектам появится после подключения соответствующей возможности.",
    agents: "Агенты",
    noAgents: "Активных исполнений нет",
    agentsHint: "Выполнение задач недоступно в этой сборке.",
    eyebrow: "ПОДКЛЮЧЕНИЕ РАБОЧЕЙ СРЕДЫ",
    title: "Надёжное подключение. Понятное начало работы.",
    intro:
      "Synapse проверяет локальное ядро перед открытием рабочей среды. Подключение и разрешение на выполнение — разные состояния.",
    native: "Подготовка приложения",
    connecting: "Подключение к локальному ядру",
    ready: "Локальное ядро подключено",
    fail: "Подключение требует внимания",
    stopping: "Остановка локального ядра",
    stopped: "Локальное ядро остановлено",
    nativeHint: "Оболочка проверит комплектную среду выполнения.",
    connectingHint:
      "Проверяем среду выполнения и согласовываем протокол связи.",
    readyHint:
      "Служебное соединение готово. Выполнение задач и доступ к проектам пока недоступны.",
    stoppingHint: "Ожидаем подтверждения завершения от ядра.",
    stoppedHint:
      "Завершение подтверждено. Откройте приложение заново для нового сеанса.",
    retry: "Проверить подключение",
    stop: "Остановить ядро",
    force: "Принудительно закрыть",
    forceWarning:
      "Закрыть приложение и управляемые локальные процессы принудительно? Это не отменяет внешние эффекты и не устанавливает исход незавершённых операций.",
    desktop: "Оболочка приложения",
    core: "Локальное ядро",
    permissions: "Разрешения на выполнение",
    unavailable: "Недоступно",
    verified: "Подключено",
    waiting: "Ожидание",
    noGrant: "Право на выполнение не выдано",
    local: "Локальные каналы",
    network: "Без сетевого порта между UI и ядром",
    session: "Сведения о сеансе",
    version: "Версия ядра",
    process: "Процесс ядра",
    profile: "Служебный профиль",
    privacy: "Локальные данные",
    privacyHint:
      "Подключение к ядру не отправляет файлы проекта, память или переписку внешней модели.",
    diagnostics: "Диагностика подключения",
    noDiagnostics: "Диагностических событий нет.",
    reply: "Последний ответ ядра",
    bottom: "Локальное служебное соединение",
    footer: "Готовность подтверждается, а не имитируется.",
  },
} as const;

export const errors: Record<Language, Record<DiagnosticCode, string>> = {
  en: {
    DESKTOP_REQUIRED:
      "Open Synapse in the desktop application to connect to its local core.",
    CALLER_REFUSED:
      "This window is not permitted to control the local connection.",
    ENVIRONMENT_UNSUPPORTED:
      "This environment cannot provide the required process containment. On Linux, launch Synapse through its systemd user service.",
    CORE_MISSING:
      "The bundled core is missing. Repair the desktop installation.",
    CORE_INTEGRITY:
      "The bundled runtime did not pass its integrity check. Repair the installation.",
    CORE_VERSION: "The core version does not match this desktop build.",
    SPAWN_FAILED: "The local core could not be started.",
    CONTAINMENT_FAILED:
      "The core could not be placed in its managed process group.",
    CORE_EXITED:
      "The core connection ended unexpectedly. Reopen the desktop after closing this session.",
    PROTOCOL_ERROR:
      "Connection data could not be verified. No new operation was authorized.",
    HANDSHAKE_TIMEOUT:
      "The core did not finish connecting within the allowed time.",
    TRANSPORT_TIMEOUT: "The core did not respond within the allowed time.",
    SHUTDOWN_TIMEOUT:
      "Shutdown was not confirmed. You may explicitly force close the desktop.",
    RESTART_REQUIRED:
      "Close and reopen the desktop before starting another core session.",
    BUSY: "The connection is handling another request. Try again shortly.",
    CORE_DIAGNOSTIC:
      "The core reported a diagnostic event. Sensitive details were excluded.",
    INTERNAL_ERROR:
      "The connection could not complete the request. Diagnostic details were kept private.",
  },
  ru: {
    DESKTOP_REQUIRED:
      "Откройте настольное приложение Synapse для подключения к локальному ядру.",
    CALLER_REFUSED: "Этому окну не разрешено управлять локальным подключением.",
    ENVIRONMENT_UNSUPPORTED:
      "Среда не обеспечивает требуемое управление группой процессов. В Linux запускайте Synapse через его пользовательскую службу systemd.",
    CORE_MISSING:
      "Комплектное ядро отсутствует. Восстановите установку приложения.",
    CORE_INTEGRITY:
      "Проверка целостности комплектной среды не пройдена. Восстановите установку.",
    CORE_VERSION: "Версия ядра не соответствует сборке приложения.",
    SPAWN_FAILED: "Не удалось запустить локальное ядро.",
    CONTAINMENT_FAILED:
      "Не удалось включить ядро в управляемую группу процессов.",
    CORE_EXITED:
      "Соединение с ядром неожиданно завершилось. Закройте сеанс и откройте приложение заново.",
    PROTOCOL_ERROR:
      "Не удалось проверить данные соединения. Новая операция не была разрешена.",
    HANDSHAKE_TIMEOUT: "Ядро не завершило подключение за отведённое время.",
    TRANSPORT_TIMEOUT: "Ядро не ответило за отведённое время.",
    SHUTDOWN_TIMEOUT:
      "Завершение не подтверждено. Можно явно выбрать принудительное закрытие.",
    RESTART_REQUIRED:
      "Закройте и откройте приложение перед запуском нового сеанса ядра.",
    BUSY: "Соединение обрабатывает другой запрос. Повторите немного позже.",
    CORE_DIAGNOSTIC:
      "Ядро сообщило о диагностическом событии. Чувствительные сведения исключены.",
    INTERNAL_ERROR:
      "Не удалось завершить запрос. Чувствительные диагностические сведения скрыты.",
  },
};
