"""
Synapse Lexer - Токенизатор языка Synapse
"""
from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Optional

class TokenType(Enum):
    # Литералы
    NUMBER = auto()
    STRING = auto()
    TRUE = auto()
    FALSE = auto()
    NULL = auto()

    # Ключевые слова
    AGENT = auto()
    FN = auto()
    LET = auto()
    IF = auto()
    ELSE = auto()
    RETURN = auto()
    TRY = auto()
    CATCH = auto()
    MODEL = auto()
    MEMORY = auto()
    THOUGHT = auto()
    FLOW = auto()
    SUPERPOSE = auto()
    DEBATE = auto()
    REFLECT = auto()
    BRANCH = auto()
    JUDGE = auto()
    ROUNDS = auto()
    LAST = auto()
    EVENTS = auto()
    FILTER = auto()
    PARALLEL = auto()
    PROMPT = auto()
    LLM = auto()
    WHILE = auto()
    FOR = auto()
    IN = auto()
    IMPORT = auto()
    AS = auto()
    POLICY = auto()
    VERIFY = auto()
    CLAIM = auto()
    CONSEQUENCE = auto()
    REQUIRE = auto()
    FORBID = auto()
    CHECK = auto()
    TEXT = auto()
    EVIDENCE = auto()
    CONFIDENCE = auto()
    SCOPE = auto()
    REASON = auto()
    RETENTION = auto()
    SEND = auto()
    RECEIVE = auto()
    TARGET = auto()
    GUARD = auto()
    REJECT = auto()
    TIMEOUT = auto()
    MIGRATE = auto()
    SPAWN = auto()
    SUSPEND = auto()
    AWAIT = auto()
    ASYNC = auto()
    INTENT = auto()
    DECLARE = auto()
    TRUST = auto()
    LEVEL = auto()
    OBSERVE = auto()
    ON = auto()
    SELF = auto()
    SOULPRINT = auto()
    VALUES = auto()
    DREAM = auto()
    SCENARIO = auto()
    TEMPERATURE = auto()
    DEPTH = auto()
    CONSTRAINTS = auto()
    INTEGRATE = auto()
    EVOLVE = auto()
    WHEN = auto()
    AFTER = auto()
    WITH = auto()
    ASSERT = auto()
    UNDER = auto()
    FAIL = auto()
    ROLLBACK = auto()
    WARN = auto()
    HALT = auto()
    TICKET = auto()
    EVOLUTION = auto()
    FRACTURE = auto()
    INTO = auto()
    CONSENSUS = auto()
    WEIGHTED = auto()
    MAJORITY = auto()
    UNANIMOUS = auto()
    FOCUS = auto()
    SOULPRINT_OVERRIDE = auto()
    STYLE = auto()
    VERSION = auto()
    PROTECTED = auto()
    RESONATE = auto()
    ASPECTS = auto()
    WINDOW = auto()
    MEASURE = auto()
    METRICS = auto()
    BIND = auto()
    COHERENCE = auto()
    STABILITY = auto()
    CONSENSUS_RATE = auto()
    RESONANCE_DRIFT = auto()
    COLLECTIVE = auto()
    DISTRIBUTED = auto()
    SWARM = auto()
    QUORUM = auto()
    CONVERGE_ON = auto()
    ROLES = auto()
    ALLOW = auto()
    PALACE = auto()
    ROOMS = auto()
    EPISODIC = auto()
    SEMANTIC = auto()
    PROCEDURAL = auto()
    DECAY_POLICY = auto()
    BACKEND = auto()
    IMPRINT = auto()
    RECALL = auto()
    CONTENT = auto()
    SOURCE = auto()
    TRACE_ID = auto()
    THRESHOLD = auto()
    LIMIT = auto()
    INTENTION = auto()
    CASCADE = auto()
    MISSION = auto()
    OBJECTIVE = auto()
    TASK = auto()
    ACTION = auto()
    PLAN = auto()
    WEAVE = auto()
    CHECKPOINT = auto()
    EVERY = auto()
    STEPS = auto()
    FAILURE = auto()
    HABIT = auto()
    PATTERN = auto()
    FREQUENCY = auto()
    PROMOTE_TO = auto()
    ACTIVATION_CONDITION = auto()
    ENERGY_COST = auto()
    CONSOLIDATE = auto()
    DURING = auto()
    FROM = auto()

    AFFECTIVE = auto()
    STATE = auto()
    DIMENSIONS = auto()
    BASELINE = auto()
    DECAY = auto()
    PER = auto()
    MINUTE = auto()
    EVENT = auto()
    VALENCE = auto()
    AROUSAL = auto()
    DOMINANCE = auto()
    DURATION = auto()
    MODULATION = auto()
    RESONANCE = auto()
    INCREASE_CAUTION = auto()
    SUPPRESS = auto()
    ELEVATE = auto()
    REDUCE_DEPTH = auto()
    TRIGGER = auto()
    MIRROR = auto()
    REGULATE = auto()
    DAMPEN = auto()
    SOMATIC = auto()
    MARKER = auto()
    GUT_FEELING = auto()
    ESCALATE_TO = auto()
    AFFECTIVE_TAG = auto()
    AFFECTIVE_DECAY = auto()
    AFFECTIVE_FILTER = auto()
    AFFECTIVE_SORT = auto()
    AFFECTIVE_ROUTING = auto()
    TAGGED = auto()
    UNTAGGED = auto()
    DAYS = auto()
    NEVER = auto()
    ASC = auto()
    DESC = auto()
    PLEASURE = auto()
    ENERGY_ALIAS = auto()
    CONTROL = auto()
    PROMOTE = auto()
    KEEP = auto()
    TAG = auto()
    AFFECTIVE_FOCUS = auto()
    REFRAME_TARGET = auto()
    COMPILE = auto()
    VM = auto()
    RUN = auto()
    GAS = auto()
    BUDGET = auto()
    COGNITIVE_BUDGET = auto()
    RESUME_FROM = auto()
    AT_IP = auto()
    BEFORE_OP = auto()
    PRIORITY = auto()
    COOLDOWN = auto()
    ENERGY = auto()
    AFFECTIVE_WEIGHTED = auto()
    AFFECTIVE_BIAS = auto()
    BIAS = auto()
    DEFAULT = auto()
    ENERGY_POOL = auto()
    CONTEXT = auto()
    MAX = auto()
    INITIAL = auto()
    RECHARGE = auto()
    REST_THRESHOLD = auto()
    HYSTERESIS_MARGIN = auto()
    ACTIVATE = auto()
    BODY = auto()
    FATIGUE = auto()
    ACTIVATIONS = auto()
    ENERGY_COST_MULTIPLIER = auto()
    REQUIRE_REST = auto()

    # Идентификаторы
    IDENTIFIER = auto()

    # Символы
    LBRACE = auto()      # {
    RBRACE = auto()      # }
    LPAREN = auto()      # (
    RPAREN = auto()      # )
    LBRACKET = auto()    # [
    RBRACKET = auto()    # ]
    COMMA = auto()       # ,
    COLON = auto()       # :
    SEMICOLON = auto()   # ;
    DOT = auto()         # .
    ARROW = auto()       # ->
    FATARROW = auto()    # =>
    PIPE = auto()        # |>
    AT = auto()          # @

    # Операторы
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    ASSIGN = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    GT = auto()
    LTE = auto()
    GTE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()

    # Специальные
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    EOF = auto()
    COMMENT = auto()

@dataclass
class Token:
    type: TokenType
    value: any
    line: int
    column: int

KEYWORDS = {
    'agent': TokenType.AGENT,
    'fn': TokenType.FN,
    'let': TokenType.LET,
    'if': TokenType.IF,
    'else': TokenType.ELSE,
    'return': TokenType.RETURN,
    'try': TokenType.TRY,
    'catch': TokenType.CATCH,
    'model': TokenType.MODEL,
    'memory': TokenType.MEMORY,
    'thought': TokenType.THOUGHT,
    'flow': TokenType.FLOW,
    'superpose': TokenType.SUPERPOSE,
    'debate': TokenType.DEBATE,
    'reflect': TokenType.REFLECT,
    'branch': TokenType.BRANCH,
    'judge': TokenType.JUDGE,
    'rounds': TokenType.ROUNDS,
    'last': TokenType.LAST,
    'events': TokenType.EVENTS,
    'filter': TokenType.FILTER,
    'parallel': TokenType.PARALLEL,
    'prompt': TokenType.PROMPT,
    'llm': TokenType.LLM,
    'while': TokenType.WHILE,
    'for': TokenType.FOR,
    'in': TokenType.IN,
    'import': TokenType.IMPORT,
    'as': TokenType.AS,
    'policy': TokenType.POLICY,
    'verify': TokenType.VERIFY,
    'claim': TokenType.CLAIM,
    'consequence': TokenType.CONSEQUENCE,
    'require': TokenType.REQUIRE,
    'forbid': TokenType.FORBID,
    'check': TokenType.CHECK,
    'text': TokenType.TEXT,
    'evidence': TokenType.EVIDENCE,
    'confidence': TokenType.CONFIDENCE,
    'scope': TokenType.SCOPE,
    'reason': TokenType.REASON,
    'retention': TokenType.RETENTION,
    'send': TokenType.SEND,
    'receive': TokenType.RECEIVE,
    'target': TokenType.TARGET,
    'guard': TokenType.GUARD,
    'reject': TokenType.REJECT,
    'timeout': TokenType.TIMEOUT,
    'migrate': TokenType.MIGRATE,
    'spawn': TokenType.SPAWN,
    'suspend': TokenType.SUSPEND,
    'await': TokenType.AWAIT,
    'async': TokenType.ASYNC,
    'intent': TokenType.INTENT,
    'declare': TokenType.DECLARE,
    'trust': TokenType.TRUST,
    'level': TokenType.LEVEL,
    'observe': TokenType.OBSERVE,
    'on': TokenType.ON,
    'self': TokenType.SELF,
    'soulprint': TokenType.SOULPRINT,
    'values': TokenType.VALUES,
    'dream': TokenType.DREAM,
    'scenario': TokenType.SCENARIO,
    'temperature': TokenType.TEMPERATURE,
    'depth': TokenType.DEPTH,
    'constraints': TokenType.CONSTRAINTS,
    'integrate': TokenType.INTEGRATE,
    'evolve': TokenType.EVOLVE,
    'when': TokenType.WHEN,
    'after': TokenType.AFTER,
    'with': TokenType.WITH,
    'assert': TokenType.ASSERT,
    'under': TokenType.UNDER,
    'fail': TokenType.FAIL,
    'rollback': TokenType.ROLLBACK,
    'warn': TokenType.WARN,
    'halt': TokenType.HALT,
    'ticket': TokenType.TICKET,
    'evolution': TokenType.EVOLUTION,
    'fracture': TokenType.FRACTURE,
    'into': TokenType.INTO,
    'consensus': TokenType.CONSENSUS,
    'weighted': TokenType.WEIGHTED,
    'majority': TokenType.MAJORITY,
    'unanimous': TokenType.UNANIMOUS,
    'focus': TokenType.FOCUS,
    'soulprint_override': TokenType.SOULPRINT_OVERRIDE,
    'style': TokenType.STYLE,
    'version': TokenType.VERSION,
    'protected': TokenType.PROTECTED,
    'resonate': TokenType.RESONATE,
    'aspects': TokenType.ASPECTS,
    'window': TokenType.WINDOW,
    'measure': TokenType.MEASURE,
    'metrics': TokenType.METRICS,
    'bind': TokenType.BIND,
    'coherence': TokenType.COHERENCE,
    'stability': TokenType.STABILITY,
    'consensus_rate': TokenType.CONSENSUS_RATE,
    'resonance_drift': TokenType.RESONANCE_DRIFT,
    'collective': TokenType.COLLECTIVE,
    'distributed': TokenType.DISTRIBUTED,
    'swarm': TokenType.SWARM,
    'quorum': TokenType.QUORUM,
    'converge_on': TokenType.CONVERGE_ON,
    'roles': TokenType.ROLES,
    'allow': TokenType.ALLOW,
    'palace': TokenType.PALACE,
    'rooms': TokenType.ROOMS,
    'episodic': TokenType.EPISODIC,
    'semantic': TokenType.SEMANTIC,
    'procedural': TokenType.PROCEDURAL,
    'decay_policy': TokenType.DECAY_POLICY,
    'backend': TokenType.BACKEND,
    'imprint': TokenType.IMPRINT,
    'recall': TokenType.RECALL,
    'content': TokenType.CONTENT,
    'source': TokenType.SOURCE,
    'trace_id': TokenType.TRACE_ID,
    'threshold': TokenType.THRESHOLD,
    'limit': TokenType.LIMIT,
    'intention': TokenType.INTENTION,
    'cascade': TokenType.CASCADE,
    'mission': TokenType.MISSION,
    'objective': TokenType.OBJECTIVE,
    'task': TokenType.TASK,
    'action': TokenType.ACTION,
    'plan': TokenType.PLAN,
    'weave': TokenType.WEAVE,
    'checkpoint': TokenType.CHECKPOINT,
    'every': TokenType.EVERY,
    'steps': TokenType.STEPS,
    'failure': TokenType.FAILURE,
    'habit': TokenType.HABIT,
    'pattern': TokenType.PATTERN,
    'frequency': TokenType.FREQUENCY,
    'promote_to': TokenType.PROMOTE_TO,
    'activation_condition': TokenType.ACTIVATION_CONDITION,
    'energy_cost': TokenType.ENERGY_COST,
    'consolidate': TokenType.CONSOLIDATE,
    'during': TokenType.DURING,
    'from': TokenType.FROM,
    'affective': TokenType.AFFECTIVE,
    'state': TokenType.STATE,
    'dimensions': TokenType.DIMENSIONS,
    'baseline': TokenType.BASELINE,
    'decay': TokenType.DECAY,
    'per': TokenType.PER,
    'minute': TokenType.MINUTE,
    'event': TokenType.EVENT,
    'valence': TokenType.VALENCE,
    'arousal': TokenType.AROUSAL,
    'dominance': TokenType.DOMINANCE,
    'duration': TokenType.DURATION,
    'modulation': TokenType.MODULATION,
    'resonance': TokenType.RESONANCE,
    'increase_caution': TokenType.INCREASE_CAUTION,
    'suppress': TokenType.SUPPRESS,
    'elevate': TokenType.ELEVATE,
    'reduce_depth': TokenType.REDUCE_DEPTH,
    'trigger': TokenType.TRIGGER,
    'mirror': TokenType.MIRROR,
    'regulate': TokenType.REGULATE,
    'dampen': TokenType.DAMPEN,
    'somatic': TokenType.SOMATIC,
    'marker': TokenType.MARKER,
    'gut_feeling': TokenType.GUT_FEELING,
    'escalate_to': TokenType.ESCALATE_TO,
    'affective_tag': TokenType.AFFECTIVE_TAG,
    'affective_decay': TokenType.AFFECTIVE_DECAY,
    'affective_filter': TokenType.AFFECTIVE_FILTER,
    'affective_sort': TokenType.AFFECTIVE_SORT,
    'affective_routing': TokenType.AFFECTIVE_ROUTING,
    'tagged': TokenType.TAGGED,
    'untagged': TokenType.UNTAGGED,
    'days': TokenType.DAYS,
    'never': TokenType.NEVER,
    'asc': TokenType.ASC,
    'desc': TokenType.DESC,
    'pleasure': TokenType.PLEASURE,
    'control': TokenType.CONTROL,
    'promote': TokenType.PROMOTE,
    'keep': TokenType.KEEP,
    'tag': TokenType.TAG,
    'affective_focus': TokenType.AFFECTIVE_FOCUS,
    'reframe_target': TokenType.REFRAME_TARGET,
    'compile': TokenType.COMPILE,
    'vm': TokenType.VM,
    'run': TokenType.RUN,
    'gas': TokenType.GAS,
    'budget': TokenType.BUDGET,
    'cognitive_budget': TokenType.COGNITIVE_BUDGET,
    'resume_from': TokenType.RESUME_FROM,
    'at_ip': TokenType.AT_IP,
    'before_op': TokenType.BEFORE_OP,
    'priority': TokenType.PRIORITY,
    'cooldown': TokenType.COOLDOWN,
    'affective_weighted': TokenType.AFFECTIVE_WEIGHTED,
    'affective_bias': TokenType.AFFECTIVE_BIAS,
    'bias': TokenType.BIAS,
    'Default': TokenType.DEFAULT,
    'default': TokenType.DEFAULT,
    'energy_pool': TokenType.ENERGY_POOL,
    'context': TokenType.CONTEXT,
    'max': TokenType.MAX,
    'initial': TokenType.INITIAL,
    'recharge': TokenType.RECHARGE,
    'rest_threshold': TokenType.REST_THRESHOLD,
    'hysteresis_margin': TokenType.HYSTERESIS_MARGIN,
    'activate': TokenType.ACTIVATE,
    'body': TokenType.BODY,
    'fatigue': TokenType.FATIGUE,
    'activations': TokenType.ACTIVATIONS,
    'energy_cost_multiplier': TokenType.ENERGY_COST_MULTIPLIER,
    'require_rest': TokenType.REQUIRE_REST,
    'energy': TokenType.ENERGY_ALIAS,
    'true': TokenType.TRUE,
    'false': TokenType.FALSE,
    'null': TokenType.NULL,
    'and': TokenType.AND,
    'or': TokenType.OR,
    'not': TokenType.NOT,
}

class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.tokens: List[Token] = []
        self.start = 0
        self.current = 0
        self.line = 1
        self.column = 1
        self.indent_stack = [0]

    def error(self, msg: str):
        raise SyntaxError(f"{msg} at line {self.line}, column {self.column}")

    def is_at_end(self) -> bool:
        return self.current >= len(self.source)

    def peek(self) -> str:
        if self.is_at_end():
            return '\0'
        return self.source[self.current]

    def peek_next(self) -> str:
        if self.current + 1 >= len(self.source):
            return '\0'
        return self.source[self.current + 1]

    def advance(self) -> str:
        char = self.source[self.current]
        self.current += 1
        if char == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def match(self, expected: str) -> bool:
        if self.is_at_end() or self.source[self.current] != expected:
            return False
        self.advance()
        return True

    def add_token(self, type: TokenType, value=None):
        self.tokens.append(Token(type, value, self.line, self.column))

    def string(self):
        while self.peek() != '"' and not self.is_at_end():
            if self.peek() == '\n':
                self.error("Unterminated string")
            self.advance()
        if self.is_at_end():
            self.error("Unterminated string")
        self.advance()  # closing "
        value = self.source[self.start + 1:self.current - 1]
        self.add_token(TokenType.STRING, value)

    def number(self):
        while self.peek().isdigit():
            self.advance()
        if self.peek() == '.' and self.peek_next().isdigit():
            self.advance()
            while self.peek().isdigit():
                self.advance()
        value = float(self.source[self.start:self.current])
        if value == int(value):
            value = int(value)
        self.add_token(TokenType.NUMBER, value)

    def identifier(self):
        while self.peek().isalnum() or self.peek() == '_':
            self.advance()
        text = self.source[self.start:self.current]
        type = KEYWORDS.get(text, TokenType.IDENTIFIER)
        # Store value for keyword operators (not, and, or)
        value = text if type in (TokenType.IDENTIFIER, TokenType.NOT, TokenType.AND, TokenType.OR) else None
        self.add_token(type, value)

    def skip_whitespace(self):
        while self.peek() in ' \t\r':
            self.advance()

    def comment(self):
        while self.peek() != '\n' and not self.is_at_end():
            self.advance()

    def handle_indent(self):
        # Простая версия: отслеживаем отступы для блоков
        pass  # В MVP используем фигурные скобки для блоков

    def scan_token(self):
        char = self.advance()

        if char == '(':
            self.add_token(TokenType.LPAREN, '(')
        elif char == ')':
            self.add_token(TokenType.RPAREN, ')')
        elif char == '{':
            self.add_token(TokenType.LBRACE, '{')
        elif char == '}':
            self.add_token(TokenType.RBRACE, '}')
        elif char == '[':
            self.add_token(TokenType.LBRACKET, '[')
        elif char == ']':
            self.add_token(TokenType.RBRACKET, ']')
        elif char == ',':
            self.add_token(TokenType.COMMA, ',')
        elif char == ':':
            self.add_token(TokenType.COLON, ':')
        elif char == ';':
            self.add_token(TokenType.SEMICOLON, ';')
        elif char == '.':
            self.add_token(TokenType.DOT, '.')
        elif char == '+':
            self.add_token(TokenType.PLUS, '+')
        elif char == '-':
            if self.match('>'):
                self.add_token(TokenType.ARROW)
            else:
                self.add_token(TokenType.MINUS, '-')
        elif char == '*':
            self.add_token(TokenType.STAR, '*')
        elif char == '/':
            if self.match('/'):
                self.comment()
            else:
                self.add_token(TokenType.SLASH, '/')
        elif char == '%':
            self.add_token(TokenType.PERCENT, '%')
        elif char == '=':
            if self.match('='):
                self.add_token(TokenType.EQ)
            elif self.match('>'):
                self.add_token(TokenType.FATARROW)
            else:
                self.add_token(TokenType.ASSIGN, '=')
        elif char == '!':
            if self.match('='):
                self.add_token(TokenType.NEQ)
            else:
                self.add_token(TokenType.NOT, '!')
        elif char == '<':
            if self.match('='):
                self.add_token(TokenType.LTE)
            else:
                self.add_token(TokenType.LT, '<')
        elif char == '>':
            if self.match('='):
                self.add_token(TokenType.GTE)
            else:
                self.add_token(TokenType.GT, '>')
        elif char == '|':
            if self.match('>'):
                self.add_token(TokenType.PIPE, '|>')
            else:
                self.error("Expected '>' after '|' for pipeline operator")
        elif char == '@':
            self.add_token(TokenType.AT, '@')
        elif char == '"':
            self.string()
        elif char.isdigit():
            self.number()
        elif char.isalpha() or char == '_':
            self.identifier()
        elif char == '\n':
            self.add_token(TokenType.NEWLINE)
        elif char in ' \t\r':
            pass  # skip whitespace between tokens
        else:
            self.error(f"Unexpected character: {char}")

    def scan_tokens(self) -> List[Token]:
        while not self.is_at_end():
            self.start = self.current
            self.scan_token()
        self.add_token(TokenType.EOF)
        return self.tokens
