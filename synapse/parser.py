
from typing import List, Optional, Dict
from .lexer import Lexer, Token, TokenType
from .ast import *

class ParseError(Exception):
    pass

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.current = 0

    def error(self, msg: str):
        token = self.peek()
        raise ParseError(f"{msg} at line {token.line}, got {token.type.name}")

    def is_at_end(self) -> bool:
        return self.peek().type == TokenType.EOF

    def peek(self) -> Token:
        return self.tokens[self.current]

    def previous(self) -> Token:
        return self.tokens[self.current - 1]

    def peek_ahead(self, offset: int) -> Token:
        idx = self.current + offset
        if idx >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[idx]

    def advance(self) -> Token:
        if not self.is_at_end():
            self.current += 1
        return self.previous()

    def check(self, type: TokenType) -> bool:
        if self.is_at_end():
            return False
        return self.peek().type == type

    def match(self, *types: TokenType) -> bool:
        for t in types:
            if self.check(t):
                self.advance()
                return True
        return False

    def consume(self, type: TokenType, msg: str) -> Token:
        if self.check(type):
            return self.advance()
        self.error(msg)

    def skip_newlines(self):
        while self.match(TokenType.NEWLINE):
            pass

    # Keywords that are commonly used as user-defined function / method names.
    # When a keyword appears immediately after `fn` or as a method name, the
    # parser treats its source text as an identifier rather than a keyword.
    _KEYWORD_AS_NAME_TYPES = {
        TokenType.RECALL, TokenType.IMPRINT, TokenType.MAX,
        TokenType.FILTER, TokenType.PROMOTE, TokenType.KEEP, TokenType.TAG,
        TokenType.SEND, TokenType.RECEIVE, TokenType.MIGRATE, TokenType.SPAWN,
        TokenType.SUSPEND, TokenType.AWAIT, TokenType.EVOLVE, TokenType.PLAN,
        TokenType.ACTION, TokenType.CONTENT, TokenType.SOURCE, TokenType.STATE,
        TokenType.REASON, TokenType.SCOPE, TokenType.TRUST, TokenType.LEVEL,
        TokenType.MEMORY, TokenType.CONTEXT, TokenType.BODY, TokenType.PATTERN,
    }

    def consume_name(self, msg: str) -> Token:
        """Consume an IDENTIFIER or any keyword that is valid as a name token.

        This allows programs to define functions or methods whose names collide
        with reserved words (e.g. ``fn recall(...)``, ``fn max(...)``).  The
        returned token's ``.value`` is always the lowercase keyword string so
        that downstream AST nodes receive a plain str name.
        """
        if self.check(TokenType.IDENTIFIER):
            return self.advance()
        if self.peek().type in self._KEYWORD_AS_NAME_TYPES:
            tok = self.advance()
            # Normalise: keyword tokens may have value=None; use the type name.
            if tok.value is None:
                tok = Token(tok.type, tok.type.name.lower(), tok.line, tok.column)
            return tok
        self.error(msg)

    def consume_binding_name(self, msg: str) -> str:
        allowed = {TokenType.IDENTIFIER, TokenType.COHERENCE, TokenType.STABILITY,
                   TokenType.CONSENSUS_RATE, TokenType.RESONANCE_DRIFT, TokenType.PALACE, TokenType.PLAN, TokenType.COHERENCE, TokenType.ENERGY, TokenType.MARKER, TokenType.STATE, TokenType.TAG}
        if self.peek().type in allowed:
            tok = self.advance()
            return str(tok.value or tok.type.name.lower())
        self.error(msg)

    def parse(self) -> Program:
        statements = []
        while not self.is_at_end():
            self.skip_newlines()
            if self.is_at_end():
                break
            stmt = self.statement()
            if stmt:
                statements.append(stmt)
        return Program(statements=statements, line=1, column=1)

    def statement(self) -> Optional[Node]:
        self.skip_newlines()
        if self.is_at_end():
            return None

        if self.check(TokenType.AGENT):
            return self.agent_def()
        if self.check(TokenType.CONTEXT):
            return self.context_block()
        if self.check(TokenType.ENERGY_POOL):
            return self.energy_pool_decl()
        if self.check(TokenType.FLOW):
            return self.flow_def()
        if self.check(TokenType.POLICY):
            return self.policy_def()
        if self.check(TokenType.AFFECTIVE):
            if self.peek_ahead(1).type == TokenType.THRESHOLD:
                return self.affective_threshold_def()
            if self.peek_ahead(1).type == TokenType.STATE:
                return self.affective_state_def()
            if self.peek_ahead(1).type == TokenType.EVENT:
                return self.affective_event_stmt()
            if self.peek_ahead(1).type == TokenType.MODULATION:
                return self.affective_modulation_stmt()
            if self.peek_ahead(1).type == TokenType.RESONANCE:
                return self.affective_resonance_stmt()
        if self.check(TokenType.SOMATIC):
            return self.somatic_marker_stmt()
        if self.check(TokenType.COMPILE) and self.peek_ahead(1).type == TokenType.VM:
            return self.compile_vm_stmt()
        if self.check(TokenType.RUN) and self.peek_ahead(1).type == TokenType.VM:
            return self.run_vm_stmt()
        if self.check(TokenType.MEMORY) and self.peek_ahead(1).type == TokenType.PALACE:
            return self.memory_palace_def()
        if self.check(TokenType.IMPRINT):
            return self.imprint_stmt()
        if self.check(TokenType.RECALL):
            return self.recall_stmt()
        if self.check(TokenType.INTENTION):
            return self.intention_cascade_def()
        if self.check(TokenType.PLAN):
            return self.plan_weave_stmt()
        if self.check(TokenType.HABIT):
            return self.habit_stmt()
        if self.check(TokenType.CONSOLIDATE):
            return self.consolidate_stmt()
        if self.check(TokenType.INTENT):
            return self.intent_def()
        if self.check(TokenType.DECLARE):
            return self.declare_intent_stmt()
        if self.check(TokenType.OBSERVE):
            return self.observe_block()
        if self.check(TokenType.EVOLVE):
            return self.evolve_stmt()
        if self.check(TokenType.FRACTURE):
            return self.fracture_stmt()
        if self.check(TokenType.RESONATE):
            return self.resonate_stmt()
        if self.check(TokenType.COLLECTIVE):
            return self.collective_dream_stmt()
        if self.check(TokenType.DISTRIBUTED):
            return self.distributed_consensus_stmt()
        if self.check(TokenType.SWARM):
            return self.swarm_fracture_stmt()
        if self.check(TokenType.REFLECT):
            if (self.peek_ahead(1).type == TokenType.ON and
                self.peek_ahead(2).type == TokenType.IDENTIFIER and
                self.peek_ahead(2).value == "fractures"):
                return self.reflect_on_fractures_stmt()
            return self.reflect_block()
        if self.check(TokenType.MEASURE):
            return self.measure_identity_coherence_stmt()
        if self.check(TokenType.ASSERT):
            return self.assert_stmt()
        if self.check(TokenType.INTEGRATE):
            return self.integrate_block()
        if self.check(TokenType.VERIFY):
            return self.verify_block()
        if self.check(TokenType.CLAIM):
            return self.claim_def()
        if self.check(TokenType.CONSEQUENCE):
            return self.consequence_def()
        if self.check(TokenType.CHECK):
            return self.check_stmt()
        if self.check(TokenType.REJECT):
            return self.reject_stmt()
        if self.check(TokenType.SEND):
            return self.send_stmt()
        if self.check(TokenType.RECEIVE):
            return self.receive_block()
        if self.check(TokenType.MIGRATE):
            return self.migrate_stmt()
        if self.check(TokenType.TRY):
            return self.try_catch_stmt()
        if self.check(TokenType.FN):
            return self.fn_def()
        if self.check(TokenType.LET):
            return self.let_stmt()
        if self.check(TokenType.IF):
            return self.if_stmt()
        if self.check(TokenType.WHILE):
            return self.while_stmt()
        if self.check(TokenType.FOR):
            return self.for_stmt()
        if self.check(TokenType.RETURN):
            return self.return_stmt()
        if self.check(TokenType.IMPORT):
            return self.import_stmt()
        return self.expr_stmt()

    def block(self) -> List[Node]:
        self.consume(TokenType.LBRACE, "Expected '{'")
        self.skip_newlines()
        statements = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            stmt = self.statement()
            if stmt:
                statements.append(stmt)
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}'")
        return statements

    def agent_def(self) -> AgentDef:
        token = self.advance()  # agent
        name = self.consume(TokenType.IDENTIFIER, "Expected agent name").value

        model = None
        memory = None
        trust_level = None
        trust_scope = []
        soulprint = None
        methods = []
        energy_pool = None

        self.consume(TokenType.LBRACE, "Expected '{' after agent name")
        self.skip_newlines()

        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self.skip_newlines()
            if self.check(TokenType.MODEL):
                self.advance()
                model = self.consume(TokenType.STRING, "Expected model name").value
            elif self.check(TokenType.MEMORY):
                self.advance()
                memory = self.consume(TokenType.STRING, "Expected memory config").value
            elif self.check(TokenType.TRUST):
                self.advance()
                if self.match(TokenType.LEVEL):
                    trust_level = self.expression()
                elif self.match(TokenType.SCOPE):
                    scope_expr = self.expression()
                    if isinstance(scope_expr, ListExpr):
                        trust_scope = [item.value if isinstance(item, Literal) else item for item in scope_expr.elements]
                    else:
                        trust_scope = [scope_expr]
                else:
                    self.error("Expected level or scope after trust")
            elif self.check(TokenType.SOULPRINT):
                soulprint = self.soulprint_def()
            elif self.check(TokenType.ENERGY_POOL):
                energy_pool = self.energy_pool_decl()
            elif self.check(TokenType.FN):
                methods.append(self.fn_def())
            else:
                self.error("Expected model, memory, trust, soulprint, or fn in agent body")
            self.skip_newlines()

        self.consume(TokenType.RBRACE, "Expected '}' after agent body")
        return AgentDef(name=name, model=model, memory=memory, energy_pool=energy_pool, trust_level=trust_level, trust_scope=trust_scope, soulprint=soulprint, methods=methods, 
                       line=token.line, column=token.column)


    def energy_pool_decl(self) -> EnergyPoolDecl:
        token = self.advance()  # energy_pool
        self.consume(TokenType.LBRACE, "Expected '{' after energy_pool")
        self.skip_newlines()
        values = {}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.MAX):
                values["max"] = self.expression()
            elif self.match(TokenType.INITIAL):
                values["initial"] = self.expression()
            elif self.match(TokenType.RECHARGE):
                amount = self.expression()
                self.consume(TokenType.PER, "Expected 'per' after recharge amount")
                every = self.expression()
                self.consume(TokenType.EVENTS, "Energy pool recharge must use event units")
                values["recharge_amount"] = amount
                values["recharge_every"] = every
            elif self.match(TokenType.REST_THRESHOLD):
                values["rest_threshold"] = self.expression()
            elif self.match(TokenType.HYSTERESIS_MARGIN):
                values["hysteresis_margin"] = self.expression()
            elif self.match(TokenType.PER):
                self.error("Unexpected 'per' in energy_pool")
            else:
                self.error("Expected max, initial, recharge, rest_threshold, or hysteresis_margin in energy_pool")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after energy_pool")
        return EnergyPoolDecl(
            max=values.get("max"),
            initial=values.get("initial"),
            recharge_amount=values.get("recharge_amount"),
            recharge_every=values.get("recharge_every"),
            rest_threshold=values.get("rest_threshold"),
            hysteresis_margin=values.get("hysteresis_margin"),
            line=token.line,
            column=token.column,
        )

    def context_block(self) -> ContextBlock:
        token = self.advance()  # context
        label = self.consume(TokenType.STRING, "Expected context label string").value
        body = self.block()
        return ContextBlock(label=label, body=body, line=token.line, column=token.column)

    def try_catch_stmt(self) -> TryCatchStmt:
        token = self.advance()  # try
        try_body = self.block()
        self.skip_newlines()
        self.consume(TokenType.CATCH, "Expected 'catch' after try block")
        self.consume(TokenType.LPAREN, "Expected '(' after catch")
        # GUARD_VIOLATION is currently lexed as an identifier.  Keep this
        # deliberately narrow: Track B.1 supports only local guard recovery.
        err_tok = self.consume(TokenType.IDENTIFIER, "Expected GUARD_VIOLATION in catch")
        catch_error = str(err_tok.value)
        catch_binding = None
        if self.match(TokenType.AS):
            catch_binding = self.consume_name("Expected catch binding name").value
        self.consume(TokenType.RPAREN, "Expected ')' after catch")
        if catch_error != "GUARD_VIOLATION":
            self.error("Only catch(GUARD_VIOLATION) is supported in alpha3e Track B.1")
        catch_body = self.block()
        return TryCatchStmt(try_body=try_body, catch_error=catch_error, catch_binding=catch_binding,
                            catch_body=catch_body, line=token.line, column=token.column)

    def fn_def(self) -> FnDef:
        token = self.advance()  # fn
        name = self.consume_name("Expected function name").value
        self.consume(TokenType.LPAREN, "Expected '(' after function name")
        params = []
        if not self.check(TokenType.RPAREN):
            params.append(self.consume_name("Expected parameter name").value)
            while self.match(TokenType.COMMA):
                params.append(self.consume_name("Expected parameter name").value)
        self.consume(TokenType.RPAREN, "Expected ')' after parameters")
        body = self.block()
        return FnDef(name=name, params=params, body=body, line=token.line, column=token.column)

    def flow_def(self) -> FlowDef:
        token = self.advance()  # flow
        name = self.consume(TokenType.IDENTIFIER, "Expected flow name").value
        body = self.block()
        return FlowDef(name=name, body=body, line=token.line, column=token.column)

    def policy_def(self) -> PolicyDef:
        token = self.advance()  # policy
        name = self.consume(TokenType.IDENTIFIER, "Expected policy name").value
        self.consume(TokenType.LBRACE, "Expected '{' after policy name")
        self.skip_newlines()
        target = None
        rules = []
        guard_params = []
        guard_body = []
        trigger = None
        cooldown = None
        max_delta = None
        guard_expr = None
        require_approval = False
        fields = {}

        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.TARGET):
                if self.match(TokenType.COLON):
                    target = self.expression()
                else:
                    target = self.expression()
            elif self.match(TokenType.REQUIRE):
                rules.append(PolicyRule(kind="require", value=self.expression(), line=self.previous().line, column=self.previous().column))
            elif self.match(TokenType.FORBID):
                rules.append(PolicyRule(kind="forbid", value=self.expression(), line=self.previous().line, column=self.previous().column))
            elif self.match(TokenType.GUARD):
                if self.check(TokenType.LPAREN):
                    guard_params = self.guard_params()
                    guard_body = self.block()
                else:
                    self.consume(TokenType.COLON, "Expected ':' after guard field")
                    guard_expr = self.expression()
                    fields["guard"] = guard_expr
            else:
                key_token = self.advance()
                allowed = {
                    TokenType.IDENTIFIER, TokenType.ON, TokenType.AFTER, TokenType.WITH,
                    TokenType.WHEN, TokenType.REASON, TokenType.SCOPE, TokenType.LEVEL, TokenType.TRIGGER, TokenType.COOLDOWN if hasattr(TokenType, "COOLDOWN") else TokenType.IDENTIFIER, TokenType.REQUIRE
                }
                if key_token.type not in allowed:
                    self.error("Expected policy field name")
                key = str(key_token.value or key_token.type.name.lower())
                self.consume(TokenType.COLON, "Expected ':' after policy field name")
                value = self.expression()
                # Optional unit sugar: cooldown: 10 events
                if key in {"cooldown", "delay"} and self.check(TokenType.EVENTS):
                    unit_tok = self.advance()
                    value = Literal(value={"value": getattr(value, "value", value), "unit": "events"}, line=unit_tok.line, column=unit_tok.column)
                elif key in {"cooldown", "delay"} and self.check(TokenType.IDENTIFIER) and self.peek().value in {"seconds", "calls"}:
                    unit_tok = self.advance()
                    value = Literal(value={"value": getattr(value, "value", value), "unit": unit_tok.value}, line=unit_tok.line, column=unit_tok.column)

                fields[key] = value
                if key == "trigger":
                    trigger = value
                elif key == "cooldown":
                    cooldown = value
                elif key == "max_delta":
                    max_delta = value
                elif key == "require_approval":
                    require_approval = self.literal_truth(value)
                else:
                    # Unknown fields stay in fields for future policy versions.
                    pass
            self.skip_newlines()

        self.consume(TokenType.RBRACE, "Expected '}' after policy body")
        return PolicyDef(
            name=name,
            target=target,
            rules=rules,
            guard_params=guard_params,
            guard_body=guard_body,
            trigger=trigger,
            cooldown=cooldown,
            max_delta=max_delta,
            guard_expr=guard_expr,
            require_approval=require_approval,
            fields=fields,
            line=token.line,
            column=token.column,
        )

    def literal_truth(self, node: Node) -> bool:
        if isinstance(node, Literal):
            return bool(node.value)
        return False

    def guard_params(self) -> List[str]:
        self.consume(TokenType.LPAREN, "Expected '(' after guard")
        params = []
        if not self.check(TokenType.RPAREN):
            params.append(self.consume(TokenType.IDENTIFIER, "Expected guard parameter name").value)
            while self.match(TokenType.COMMA):
                params.append(self.consume(TokenType.IDENTIFIER, "Expected guard parameter name").value)
        self.consume(TokenType.RPAREN, "Expected ')' after guard parameters")
        return params

    def reject_stmt(self) -> RejectStmt:
        token = self.advance()  # reject
        message = None
        if not self.check(TokenType.NEWLINE) and not self.check(TokenType.RBRACE) and not self.is_at_end():
            message = self.expression()
        return RejectStmt(message=message, line=token.line, column=token.column)

    def intent_def(self) -> IntentDef:
        token = self.advance()  # intent
        name = self.consume(TokenType.IDENTIFIER, "Expected intent name").value
        self.consume(TokenType.LBRACE, "Expected '{' after intent name")
        self.skip_newlines()
        fields = {}
        allowed = {TokenType.IDENTIFIER, TokenType.TARGET, TokenType.REQUIRE, TokenType.FORBID,
                   TokenType.CHECK, TokenType.TEXT, TokenType.EVIDENCE, TokenType.CONFIDENCE,
                   TokenType.SCOPE, TokenType.REASON, TokenType.RETENTION, TokenType.LEVEL,
                   TokenType.VALUES, TokenType.SCENARIO, TokenType.DEPTH, TokenType.CONSTRAINTS, TokenType.WITH, TokenType.AFTER, TokenType.ACTION, TokenType.MISSION, TokenType.OBJECTIVE, TokenType.TASK, TokenType.CONTENT, TokenType.SOURCE, TokenType.TRACE_ID}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            key_token = self.advance()
            if key_token.type not in allowed:
                self.error("Expected intent field name")
            key = str(key_token.value or key_token.type.name.lower())
            fields[key] = self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after intent body")
        return IntentDef(name=name, fields=fields, line=token.line, column=token.column)

    def declare_intent_stmt(self) -> DeclareIntentStmt:
        token = self.advance()  # declare
        self.consume(TokenType.INTENT, "Expected 'intent' after declare")
        name = self.consume(TokenType.IDENTIFIER, "Expected declared intent name").value
        return DeclareIntentStmt(name=name, line=token.line, column=token.column)

    def observe_block(self) -> ObserveBlock:
        token = self.advance()  # observe
        target = self.expression()
        self.consume(TokenType.LBRACE, "Expected '{' after observe target")
        self.skip_newlines()
        handlers = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            self.consume(TokenType.ON, "Expected 'on' in observe block")
            ev_tok = self.advance()
            if ev_tok.type not in {TokenType.IDENTIFIER, TokenType.STRING, TokenType.CLAIM, TokenType.CHECK, TokenType.RECEIVE, TokenType.SEND}:
                self.error("Expected event type after on")
            event_type = str(ev_tok.value or ev_tok.type.name.lower())
            self.consume(TokenType.FATARROW, "Expected '=>' after observed event type")
            binding = self.consume(TokenType.IDENTIFIER, "Expected event binding name").value
            body = self.block()
            handlers.append(ObserveHandler(event_type=event_type, binding=binding, body=body, line=token.line, column=token.column))
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after observe block")
        return ObserveBlock(target=target, handlers=handlers, line=token.line, column=token.column)

    def verify_block(self) -> VerifyBlock:
        token = self.advance()  # verify
        self.consume(TokenType.LBRACE, "Expected '{' after verify")
        self.skip_newlines()
        checks = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.check(TokenType.CHECK):
                checks.append(self.check_stmt())
            else:
                # Allow bare boolean expressions as checks.
                checks.append(CheckStmt(condition=self.expression(), line=self.peek().line, column=self.peek().column))
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after verify block")
        return VerifyBlock(checks=checks, line=token.line, column=token.column)

    def check_stmt(self) -> CheckStmt:
        token = self.advance()  # check
        condition = self.expression()
        message = None
        if self.match(TokenType.COMMA):
            message = self.expression()
        return CheckStmt(condition=condition, message=message, line=token.line, column=token.column)

    def claim_def(self) -> ClaimDef:
        token = self.advance()  # claim
        name = self.consume(TokenType.IDENTIFIER, "Expected claim name").value
        self.consume(TokenType.LBRACE, "Expected '{' after claim name")
        self.skip_newlines()
        text = evidence = confidence = None
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.TEXT):
                text = self.expression()
            elif self.match(TokenType.EVIDENCE):
                evidence = self.expression()
            elif self.match(TokenType.CONFIDENCE):
                confidence = self.expression()
            else:
                self.error("Expected text, evidence, or confidence in claim body")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after claim body")
        return ClaimDef(name=name, text=text, evidence=evidence, confidence=confidence, line=token.line, column=token.column)

    def consequence_def(self) -> ConsequenceDef:
        token = self.advance()  # consequence
        name = self.consume(TokenType.IDENTIFIER, "Expected consequence name").value
        self.consume(TokenType.LBRACE, "Expected '{' after consequence name")
        self.skip_newlines()
        fields = {}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            key_token = self.advance()
            allowed = {TokenType.IDENTIFIER, TokenType.REQUIRE, TokenType.FORBID, TokenType.CHECK,
                       TokenType.TEXT, TokenType.EVIDENCE, TokenType.CONFIDENCE, TokenType.SCOPE,
                       TokenType.REASON, TokenType.RETENTION, TokenType.TRUST, TokenType.LEVEL, TokenType.POLICY, TokenType.TARGET}
            if key_token.type not in allowed:
                self.error("Expected consequence field name")
            fields[str(key_token.value or key_token.type.name.lower())] = self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after consequence body")
        return ConsequenceDef(name=name, fields=fields, line=token.line, column=token.column)

    def governed_field_block(self) -> Dict[str, Node]:
        self.consume(TokenType.LBRACE, "Expected '{' after governed memory write")
        self.skip_newlines()
        fields = {}
        allowed = {TokenType.IDENTIFIER, TokenType.SCOPE, TokenType.REASON, TokenType.RETENTION,
                   TokenType.TEXT, TokenType.EVIDENCE, TokenType.CONFIDENCE, TokenType.GUARD}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            key_token = self.advance()
            if key_token.type not in allowed:
                self.error("Expected governance field name")
            fields[str(key_token.value or key_token.type.name.lower())] = self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after governed field block")
        return fields

    def send_stmt(self) -> SendStmt:
        token = self.advance()  # send
        receiver = self.primary()
        self.consume(TokenType.DOT, "Expected '.' after send receiver")
        method_token = self.consume(TokenType.IDENTIFIER, "Expected target method after send receiver")
        self.consume(TokenType.LPAREN, "Expected '(' after send method")
        args = []
        if not self.check(TokenType.RPAREN):
            args.append(self.expression())
            while self.match(TokenType.COMMA):
                args.append(self.expression())
        self.consume(TokenType.RPAREN, "Expected ')' after send arguments")
        return SendStmt(receiver=receiver, method=method_token.value, args=args, line=token.line, column=token.column)

    def receive_block(self) -> ReceiveBlock:
        token = self.advance()  # receive
        timeout_expr = None
        if self.match(TokenType.TIMEOUT):
            timeout_expr = self.expression()
        self.consume(TokenType.LBRACE, "Expected '{' after receive")
        self.skip_newlines()
        patterns = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            sender = self.consume(TokenType.IDENTIFIER, "Expected sender binding name in receive pattern").value
            self.consume(TokenType.FATARROW, "Expected '=>' in receive pattern")
            target = self.consume(TokenType.IDENTIFIER, "Expected payload binding name in receive pattern").value
            body = self.block()
            patterns.append(ReceivePattern(sender_var=sender, target_var=target, body=body, line=token.line, column=token.column))
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after receive block")
        else_body = []
        self.skip_newlines()
        if self.match(TokenType.ELSE):
            else_body = self.block()
        return ReceiveBlock(patterns=patterns, timeout=timeout_expr, else_body=else_body, line=token.line, column=token.column)

    def migrate_stmt(self) -> MigrateStmt:
        token = self.advance()  # migrate
        target = self.expression()
        return MigrateStmt(target=target, line=token.line, column=token.column)

    def fracture_stmt(self) -> FractureStmt:
        token = self.advance()  # fracture
        target = self.expression()
        self.consume(TokenType.INTO, "Expected 'into' after fracture target")
        self.consume(TokenType.LBRACE, "Expected '{' after into")
        self.skip_newlines()
        subagents = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            subagents.append(self.subagent_def())
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after subagent definitions")

        fracture_id = None
        if self.match(TokenType.AS):
            if self.check(TokenType.STRING):
                fracture_id = self.advance().value
            else:
                fracture_id = self.consume(TokenType.IDENTIFIER, "Expected fracture id after 'as'").value

        consensus = "weighted"
        consensus_config = None
        if self.match(TokenType.CONSENSUS):
            if self.match(TokenType.WEIGHTED):
                consensus = "weighted"
            elif self.match(TokenType.MAJORITY):
                consensus = "majority"
            elif self.match(TokenType.UNANIMOUS):
                consensus = "unanimous"
            elif self.match(TokenType.AFFECTIVE_WEIGHTED):
                consensus = "affective_weighted"
                self.consume(TokenType.LPAREN, "Expected '(' after affective_weighted")
                mood_ref = self.consume(TokenType.IDENTIFIER, "Expected mood variable in affective_weighted(...)").value
                self.consume(TokenType.RPAREN, "Expected ')' after affective_weighted mood variable")
                biases = self.affective_bias_mapping()
                consensus_config = AffectiveWeightedConsensus(mood_ref=mood_ref, biases=biases, line=token.line, column=token.column)
            elif self.match(TokenType.IDENTIFIER):
                consensus = self.previous().value
            else:
                self.error("Expected weighted, majority, unanimous, or affective_weighted after consensus")

        integration = None
        if self.match(TokenType.INTEGRATE):
            integration = self.block()

        return FractureStmt(
            target=target, subagents=subagents, fracture_id=fracture_id,
            consensus_strategy=consensus, consensus_config=consensus_config, integration_clause=integration,
            line=token.line, column=token.column
        )

    def affective_bias_mapping(self) -> Dict[str, Node]:
        self.consume(TokenType.LBRACE, "Expected '{' after affective_weighted(...) bias mapping")
        self.skip_newlines()
        biases: Dict[str, Node] = {}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.DEFAULT):
                name = "Default"
            else:
                name = self.consume(TokenType.IDENTIFIER, "Expected branch name or Default in bias mapping").value
            self.consume(TokenType.BIAS, "Expected 'bias' after branch name in affective_weighted mapping")
            biases[name] = self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after affective_weighted bias mapping")
        return biases

    def subagent_def(self) -> SubAgentDef:
        name = self.consume(TokenType.IDENTIFIER, "Expected subagent name").value
        self.consume(TokenType.LBRACE, "Expected '{' after subagent name")
        self.skip_newlines()
        focus = None
        override = {}
        body = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.FOCUS):
                focus = self.consume(TokenType.STRING, "Expected focus string").value
            elif self.match(TokenType.SOULPRINT_OVERRIDE):
                override = self.parse_soulprint_override()
            else:
                stmt = self.statement()
                if stmt:
                    body.append(stmt)
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after subagent body")
        return SubAgentDef(name=name, focus=focus, soulprint_override=override, body=body)

    def parse_soulprint_override(self) -> dict:
        self.consume(TokenType.LBRACE, "Expected '{' after soulprint_override")
        self.skip_newlines()
        override = {}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.VALUES):
                self.consume(TokenType.COLON, "Expected ':' after values")
                override["values"] = self.parse_soulprint_values_list()
            elif self.match(TokenType.IDENTIFIER, TokenType.STYLE, TokenType.VERSION, TokenType.PROTECTED, TokenType.MEMORY):
                key = str(self.previous().value or self.previous().type.name.lower())
                self.consume(TokenType.COLON, "Expected ':' after soulprint override field")
                val = self.expression()
                override[key] = val
            else:
                self.error("Expected soulprint_override field")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after soulprint_override")
        return override

    def parse_soulprint_values_list(self) -> dict:
        self.consume(TokenType.LBRACKET, "Expected '[' after values ':'")
        values = {}
        while not self.check(TokenType.RBRACKET) and not self.is_at_end():
            key = self.consume(TokenType.IDENTIFIER, "Expected value key").value
            self.consume(TokenType.COLON, "Expected ':' after value key")
            val = self.consume(TokenType.NUMBER, "Expected numeric soulprint value").value
            values[key] = float(val)
            if not self.match(TokenType.COMMA):
                break
        self.consume(TokenType.RBRACKET, "Expected ']' after soulprint values")
        return values

    def let_stmt(self) -> LetStmt:
        token = self.advance()  # let
        if self.match(TokenType.IDENTIFIER):
            name = self.previous().value
        elif self.match(TokenType.SELF):
            name = "self"
        elif self.match(TokenType.PLAN):
            name = "plan"
        elif self.match(TokenType.PALACE):
            name = "palace"
        elif self.match(TokenType.CONTENT):
            name = "content"
        elif self.match(TokenType.ACTION):
            name = "action"
        else:
            self.error("Expected variable name")
        self.consume(TokenType.ASSIGN, "Expected '=' after variable name")
        value = self.expression()
        return LetStmt(name=name, value=value, line=token.line, column=token.column)

    def if_stmt(self) -> IfStmt:
        token = self.advance()  # if
        condition = self.expression()
        then_body = self.block()
        else_body = []
        if self.match(TokenType.ELSE):
            else_body = self.block()
        return IfStmt(condition=condition, then_body=then_body, else_body=else_body,
                     line=token.line, column=token.column)

    def while_stmt(self) -> WhileStmt:
        token = self.advance()  # while
        condition = self.expression()
        body = self.block()
        return WhileStmt(condition=condition, body=body, line=token.line, column=token.column)

    def for_stmt(self) -> ForStmt:
        token = self.advance()  # for
        var = self.consume(TokenType.IDENTIFIER, "Expected loop variable").value
        self.consume(TokenType.IN, "Expected 'in' after loop variable")
        iterable = self.expression()
        body = self.block()
        return ForStmt(var=var, iterable=iterable, body=body, line=token.line, column=token.column)

    def return_stmt(self) -> ReturnStmt:
        token = self.advance()  # return
        value = None
        if not self.check(TokenType.NEWLINE) and not self.check(TokenType.RBRACE) and not self.is_at_end():
            value = self.expression()
        return ReturnStmt(value=value, line=token.line, column=token.column)

    def import_stmt(self) -> ImportStmt:
        token = self.advance()  # import
        module = self.consume(TokenType.STRING, "Expected module path").value
        alias = None
        if self.match(TokenType.AS):
            alias = self.consume(TokenType.IDENTIFIER, "Expected alias").value
        return ImportStmt(module=module, alias=alias, line=token.line, column=token.column)

    def expr_stmt(self) -> Node:
        expr = self.expression()
        # Location-transparent async actor send: actor_ref => method(args)
        if self.match(TokenType.FATARROW):
            method_token = self.consume(TokenType.IDENTIFIER, "Expected async target method after '=>'")
            self.consume(TokenType.LPAREN, "Expected '(' after async method")
            args = []
            if not self.check(TokenType.RPAREN):
                args.append(self.expression())
                while self.match(TokenType.COMMA):
                    args.append(self.expression())
            self.consume(TokenType.RPAREN, "Expected ')' after async send arguments")
            return SendStmt(receiver=expr, method=method_token.value, args=args, async_send=True, line=expr.line, column=expr.column)
        return ExprStmt(expr=expr, line=expr.line, column=expr.column)

    def expression(self) -> Node:
        return self.assignment()

    def assignment(self) -> Node:
        expr = self.pipeline_expr()
        if self.match(TokenType.ASSIGN):
            value = self.assignment()
            if isinstance(expr, Variable):
                return AssignStmt(target=expr.name, value=value, line=expr.line, column=expr.column)
            if isinstance(expr, MemberAccess):
                return MemberAssignStmt(target=expr.obj, member=expr.member, value=value, line=expr.line, column=expr.column)
            self.error("Invalid assignment target")
        return expr

    def pipeline_expr(self) -> Node:
        expr = self.or_expr()
        while self.match(TokenType.PIPE):
            token = self.previous()
            func_name = self.consume(TokenType.IDENTIFIER, "Expected function name after '|>'").value
            expr = CallExpr(
                callee=Variable(name=func_name, line=token.line, column=token.column),
                args=[expr],
                line=token.line,
                column=token.column,
            )
        return expr

    def or_expr(self) -> Node:
        expr = self.and_expr()
        while self.match(TokenType.OR):
            op = self.previous()
            right = self.and_expr()
            expr = BinaryExpr(left=expr, op="or", right=right, line=op.line, column=op.column)
        return expr

    def and_expr(self) -> Node:
        expr = self.equality()
        while self.match(TokenType.AND):
            op = self.previous()
            right = self.equality()
            expr = BinaryExpr(left=expr, op="and", right=right, line=op.line, column=op.column)
        return expr

    def equality(self) -> Node:
        expr = self.comparison()
        while self.match(TokenType.EQ, TokenType.NEQ):
            op = self.previous()
            right = self.comparison()
            expr = BinaryExpr(left=expr, op=op.type.name.lower(), right=right, line=op.line, column=op.column)
        return expr

    def comparison(self) -> Node:
        expr = self.term()
        while self.match(TokenType.GT, TokenType.GTE, TokenType.LT, TokenType.LTE):
            op = self.previous()
            right = self.term()
            expr = BinaryExpr(left=expr, op=op.type.name.lower(), right=right, line=op.line, column=op.column)
        return expr

    def term(self) -> Node:
        expr = self.factor()
        while self.match(TokenType.MINUS, TokenType.PLUS):
            op = self.previous()
            right = self.factor()
            expr = BinaryExpr(left=expr, op=op.value, right=right, line=op.line, column=op.column)
        return expr

    def factor(self) -> Node:
        expr = self.unary()
        while self.match(TokenType.SLASH, TokenType.STAR, TokenType.PERCENT):
            op = self.previous()
            right = self.unary()
            expr = BinaryExpr(left=expr, op=op.value, right=right, line=op.line, column=op.column)
        return expr

    def unary(self) -> Node:
        if self.match(TokenType.NOT, TokenType.MINUS):
            op = self.previous()
            expr = self.unary()
            return UnaryExpr(op=op.value, operand=expr, line=op.line, column=op.column)
        return self.call()

    def call(self) -> Node:
        expr = self.primary()
        while True:
            if self.match(TokenType.LPAREN):
                args = []
                if not self.check(TokenType.RPAREN):
                    args.append(self.expression())
                    while self.match(TokenType.COMMA):
                        args.append(self.expression())
                self.consume(TokenType.RPAREN, "Expected ')' after arguments")
                expr = CallExpr(callee=expr, args=args, line=expr.line, column=expr.column)
            elif self.match(TokenType.DOT):
                member_token = self.advance()
                allowed_members = {TokenType.IDENTIFIER, TokenType.TEXT, TokenType.EVIDENCE,
                                   TokenType.CONFIDENCE, TokenType.SCOPE, TokenType.REASON,
                                   TokenType.RETENTION, TokenType.TRUST, TokenType.LEVEL, TokenType.POLICY, TokenType.TARGET,
                                   TokenType.JUDGE, TokenType.ROUNDS, TokenType.LAST, TokenType.EVENTS, TokenType.FILTER,
                                   TokenType.VALUES, TokenType.SELF, TokenType.DEPTH, TokenType.SCENARIO, TokenType.CONSTRAINTS,
                                   TokenType.UNDER, TokenType.AFTER, TokenType.WHEN, TokenType.WITH,
                                   TokenType.ASPECTS, TokenType.WINDOW, TokenType.COHERENCE, TokenType.STABILITY,
                                   TokenType.CONSENSUS_RATE, TokenType.RESONANCE_DRIFT, TokenType.METRICS, TokenType.BIND,
                                   TokenType.SEMANTIC, TokenType.EPISODIC, TokenType.PROCEDURAL, TokenType.PALACE,
                                   TokenType.SUPPRESS, TokenType.ELEVATE, TokenType.VALENCE, TokenType.AROUSAL,
                                   TokenType.DOMINANCE, TokenType.MARKER, TokenType.ESCALATE_TO, TokenType.TRACE_ID,
                                   # Common user-defined method names that collide with keywords:
                                   TokenType.RECALL, TokenType.IMPRINT, TokenType.SEND, TokenType.RECEIVE,
                                   TokenType.SPAWN, TokenType.MIGRATE, TokenType.EVOLVE, TokenType.PLAN,
                                   TokenType.ACTION, TokenType.CONTENT, TokenType.SOURCE, TokenType.STATE,
                                   TokenType.BODY, TokenType.CONTEXT, TokenType.MAX, TokenType.PATTERN,
                                   TokenType.MEMORY, TokenType.PROMOTE, TokenType.KEEP, TokenType.TAG,
                                   }
                if member_token.type not in allowed_members:
                    self.error("Expected property name after '.'")
                name = str(member_token.value or member_token.type.name.lower())
                expr = MemberAccess(obj=expr, member=name, line=expr.line, column=expr.column)
            elif self.match(TokenType.LBRACKET):
                index = self.expression()
                self.consume(TokenType.RBRACKET, "Expected ']' after index")
                expr = CallExpr(
                    callee=MemberAccess(obj=expr, member="__getitem__", line=expr.line, column=expr.column),
                    args=[index],
                    line=expr.line, column=expr.column
                )
            else:
                break
        return expr

    def primary(self) -> Node:
        if self.match(TokenType.TRUE):
            return Literal(value=True, line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.FALSE):
            return Literal(value=False, line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.NULL):
            return Literal(value=None, line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.NUMBER):
            return Literal(value=self.previous().value, line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.STRING):
            return Literal(value=self.previous().value, line=self.previous().line, column=self.previous().column)

        if self.check(TokenType.PROMPT):
            return self.prompt_expr()
        if self.check(TokenType.LLM):
            return self.llm_call()
        if self.check(TokenType.THOUGHT):
            return self.thought_block()
        if self.check(TokenType.SUPERPOSE):
            return self.superpose_block()
        if self.check(TokenType.DEBATE):
            # `debate { ... }` is a block; `debate.round()` inside branches is a meta-context variable.
            if self.current + 1 < len(self.tokens) and self.tokens[self.current + 1].type == TokenType.LBRACE:
                return self.debate_block()
            tok = self.advance()
            return Variable(name="debate", line=tok.line, column=tok.column)
        if self.check(TokenType.REFLECT):
            return self.reflect_block()
        if self.check(TokenType.DREAM):
            return self.dream_block()
        if self.check(TokenType.FRACTURE):
            return self.fracture_stmt()
        if self.check(TokenType.COLLECTIVE):
            return self.collective_dream_stmt()
        if self.check(TokenType.DISTRIBUTED):
            return self.distributed_consensus_stmt()
        if self.check(TokenType.SWARM):
            return self.swarm_fracture_stmt()
        if self.check(TokenType.MEMORY):
            return self.memory_access()
        if self.check(TokenType.SPAWN):
            return self.spawn_expr()
        if self.check(TokenType.AWAIT):
            return self.await_expr()
        if self.check(TokenType.SUSPEND):
            return self.suspend_expr()
        if self.check(TokenType.LBRACKET):
            return self.list_expr()
        if self.check(TokenType.LBRACE):
            return self.dict_expr()

        if self.match(TokenType.COHERENCE):
            return Variable(name="coherence", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.STABILITY):
            return Variable(name="stability", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.CONSENSUS_RATE):
            return Variable(name="consensus_rate", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.RESONANCE_DRIFT):
            return Variable(name="resonance_drift", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.PALACE):
            return Variable(name="palace", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.SOURCE):
            return Variable(name="source", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.CONTENT):
            return Variable(name="content", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.ACTION):
            return Variable(name="action", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.PLAN):
            return Variable(name="plan", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.PROCEDURAL):
            return Variable(name="procedural", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.SEMANTIC):
            return Variable(name="semantic", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.EPISODIC):
            return Variable(name="episodic", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.MARKER):
            return Variable(name="marker", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.STATE):
            return Variable(name="state", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.ENERGY):
            return Variable(name="energy", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.VALENCE, TokenType.PLEASURE):
            return Variable(name="valence", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.AROUSAL, TokenType.ENERGY_ALIAS):
            return Variable(name="arousal", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.DOMINANCE, TokenType.CONTROL):
            return Variable(name="dominance", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.IDENTIFIER):
            return Variable(name=self.previous().value, line=self.previous().line, column=self.previous().column)

        # Keywords that may appear as plain function calls in expression position.
        # e.g. max(nums), sorted(nums), recall(pattern) when used as builtins.
        _callable_keywords = {
            TokenType.MAX: "max",
            TokenType.RECALL: "recall",
            TokenType.IMPRINT: "imprint",
            TokenType.FILTER: "filter",
            TokenType.PROMOTE: "promote",
        }
        if self.peek().type in _callable_keywords:
            kw_tok = self.advance()
            name = _callable_keywords[kw_tok.type]
            return Variable(name=name, line=kw_tok.line, column=kw_tok.column)

        # Keywords that may appear as plain variable references in expression position.
        _variable_keywords = {
            TokenType.PATTERN: "pattern",
            TokenType.BODY: "body",
            TokenType.CONTEXT: "context",
        }
        for kw_type, kw_name in _variable_keywords.items():
            if self.check(kw_type):
                tok = self.advance()
                return Variable(name=kw_name, line=tok.line, column=tok.column)

        if self.match(TokenType.SELF):
            return Variable(name="self", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.SOULPRINT):
            return Variable(name="soulprint", line=self.previous().line, column=self.previous().column)
        if self.match(TokenType.CONSENSUS):
            return Variable(name="consensus", line=self.previous().line, column=self.previous().column)

        if self.match(TokenType.LPAREN):
            expr = self.expression()
            self.consume(TokenType.RPAREN, "Expected ')' after expression")
            return expr

        self.error(f"Unexpected token: {self.peek().type.name}")


    def spawn_expr(self) -> SpawnExpr:
        token = self.advance()  # spawn
        callee = self.call()
        return SpawnExpr(callee=callee, line=token.line, column=token.column)

    def await_expr(self) -> AwaitExpr:
        token = self.advance()  # await
        expr = self.expression()
        return AwaitExpr(expr=expr, line=token.line, column=token.column)

    def suspend_expr(self) -> SuspendExpr:
        token = self.advance()  # suspend
        request = self.expression()
        return SuspendExpr(request=request, line=token.line, column=token.column)

    def prompt_expr(self) -> PromptExpr:
        token = self.advance()  # prompt
        template = self.consume(TokenType.STRING, "Expected prompt template string").value
        return PromptExpr(template=template, line=token.line, column=token.column)

    def llm_call(self) -> LLMCall:
        token = self.advance()  # llm
        model = None
        temperature = 0.7
        max_tokens = 100

        # Standard form: llm(prompt "...")
        if self.match(TokenType.LPAREN):
            prompt = self.expression()
            if self.match(TokenType.COMMA):
                if self.check(TokenType.STRING):
                    model = self.advance().value
            self.consume(TokenType.RPAREN, "Expected ')' after llm arguments")
        else:
            # Sugar: llm "Do X with {var}"
            if self.match(TokenType.STRING):
                prompt = Literal(value=self.previous().value, line=token.line, column=token.column)
            else:
                prompt = self.expression()

        return LLMCall(prompt=prompt, model=model, temperature=temperature, 
                      max_tokens=max_tokens, line=token.line, column=token.column)

    def thought_block(self) -> ThoughtBlock:
        token = self.advance()  # thought
        self.consume(TokenType.LBRACE, "Expected '{' after thought")
        self.skip_newlines()
        steps = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.IDENTIFIER) and self.previous().value == "step":
                steps.append(self.expression())
            else:
                steps.append(self.expression())
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after thought block")

        aggregator = "chain"
        if self.match(TokenType.IDENTIFIER) and self.previous().value == "aggregate":
            aggregator = self.consume(TokenType.STRING, "Expected aggregator").value

        return ThoughtBlock(steps=steps, aggregator=aggregator, line=token.line, column=token.column)

    def superpose_block(self) -> SuperposeBlock:
        token = self.advance()  # superpose
        self.consume(TokenType.LBRACE, "Expected '{' after superpose")
        self.skip_newlines()
        branches = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.check(TokenType.BRANCH):
                branches.append(self.branch_def())
            else:
                self.error("Expected branch in superpose block")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after superpose block")

        selector = "first"
        if self.match(TokenType.IDENTIFIER) and self.previous().value == "select":
            selector = self.consume(TokenType.STRING, "Expected selector").value

        return SuperposeBlock(branches=branches, selector=selector, line=token.line, column=token.column)

    def debate_block(self) -> DebateBlock:
        token = self.advance()  # debate
        self.consume(TokenType.LBRACE, "Expected '{' after debate")
        self.skip_newlines()
        branches = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.check(TokenType.BRANCH):
                branches.append(self.branch_def())
            else:
                self.error("Expected branch in debate block")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after debate block")

        judge = Literal(value="neutral_judge", line=token.line, column=token.column)
        rounds = Literal(value=1, line=token.line, column=token.column)
        if self.match(TokenType.JUDGE):
            judge = self.expression()
        if self.match(TokenType.ROUNDS):
            rounds = self.expression()
        affective_bias = None
        if self.match(TokenType.AFFECTIVE_BIAS):
            self.consume(TokenType.LPAREN, "Expected '(' after affective_bias")
            mood_ref = self.consume(TokenType.IDENTIFIER, "Expected mood variable in affective_bias(...)").value
            self.consume(TokenType.RPAREN, "Expected ')' after affective_bias")
            affective_bias = AffectiveBias(mood_ref=mood_ref, line=token.line, column=token.column)
        return DebateBlock(branches=branches, judge=judge, rounds=rounds, affective_bias=affective_bias, line=token.line, column=token.column)

    def reflect_block(self) -> ReflectBlock:
        token = self.advance()  # reflect
        target = None
        if self.match(TokenType.ON):
            if self.match(TokenType.SELF):
                target = "self"
            elif self.match(TokenType.MEMORY):
                target = "memory"
            elif self.match(TokenType.VALUES):
                target = "values"
            else:
                target = self.consume(TokenType.IDENTIFIER, "Expected reflect target after on").value
        self.consume(TokenType.LBRACE, "Expected '{' after reflect")
        self.skip_newlines()
        last = None
        filter_condition = None
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.LAST):
                last = self.expression()
                if self.match(TokenType.EVENTS):
                    pass
            elif self.match(TokenType.FILTER):
                filter_condition = self.expression()
            elif self.match(TokenType.EVENTS):
                pass
            else:
                self.error("Expected last/events/filter in reflect block")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after reflect block")
        return ReflectBlock(target=target, last=last, filter_condition=filter_condition, line=token.line, column=token.column)




    # --- v1.9 Cognitive Continuity / Memory Palace parsing ---
    def room_name_token(self) -> str:
        if self.match(TokenType.EPISODIC):
            return "episodic"
        if self.match(TokenType.SEMANTIC):
            return "semantic"
        if self.match(TokenType.PROCEDURAL):
            return "procedural"
        if self.match(TokenType.IDENTIFIER):
            return str(self.previous().value)
        self.error("Expected memory room name")


    def v20_key_name(self) -> str:
        tok = self.advance()
        return str(tok.value or tok.type.name.lower())

    def v20_number_or_expr(self) -> Node:
        return self.expression()

    def affective_threshold_def(self) -> AffectiveThresholdDef:
        token = self.advance()  # affective
        self.consume(TokenType.THRESHOLD, "Expected 'threshold' after affective")
        name = self.consume(TokenType.STRING, "Expected threshold name string").value if self.check(TokenType.STRING) else self.consume(TokenType.IDENTIFIER, "Expected threshold name").value
        self.consume(TokenType.LBRACE, "Expected '{' after affective threshold name")
        self.skip_newlines()
        condition = None
        for_events = None
        cooldown = None
        priority = "medium"
        action = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.WHEN):
                condition = self.parse_affective_filter_expr()
            elif self.match(TokenType.FOR):
                n = self.consume(TokenType.NUMBER, "Expected event count after for")
                for_events = int(n.value)
                self.consume(TokenType.EVENTS, "Expected events after threshold for N")
            elif self.match(TokenType.COOLDOWN) if hasattr(TokenType, 'COOLDOWN') else False:
                n = self.consume(TokenType.NUMBER, "Expected event count after cooldown")
                cooldown = int(n.value)
                self.consume(TokenType.EVENTS, "Expected events after cooldown N")
            elif self.match(TokenType.IDENTIFIER) and self.previous().value == "cooldown":
                n = self.consume(TokenType.NUMBER, "Expected event count after cooldown")
                cooldown = int(n.value)
                self.consume(TokenType.EVENTS, "Expected events after cooldown N")
            elif self.match(TokenType.PRIORITY):
                if self.match(TokenType.IDENTIFIER):
                    priority = str(self.previous().value)
                else:
                    priority = self.consume(TokenType.IDENTIFIER, "Expected priority level").value
            elif self.match(TokenType.IDENTIFIER) and self.previous().value == "priority":
                priority = self.consume(TokenType.IDENTIFIER, "Expected priority level").value
            elif self.match(TokenType.ACTION):
                action = self.block()
            else:
                self.error("Expected when, for, cooldown, priority, or action in affective threshold")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after affective threshold")
        if condition is None:
            self.error("affective threshold requires a when condition")
        if priority not in {"low", "medium", "high", "critical"}:
            self.error("threshold priority must be low, medium, high, or critical")
        return AffectiveThresholdDef(name=name, condition=condition, for_events=for_events, cooldown=cooldown, priority=priority, action=action, line=token.line, column=token.column)

    def affective_state_def(self) -> AffectiveStateDef:
        token = self.advance()  # affective
        self.consume(TokenType.STATE, "Expected 'state' after affective")
        name = self.consume(TokenType.STRING, "Expected affective state name string").value if self.check(TokenType.STRING) else self.consume(TokenType.IDENTIFIER, "Expected affective state name").value
        self.consume(TokenType.LBRACE, "Expected '{' after affective state name")
        self.skip_newlines()
        dimensions = {}
        baseline = {}
        decay = 0.0
        decay_unit = "minute"
        binding = "mood"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.DIMENSIONS):
                self.consume(TokenType.LBRACE, "Expected '{' after dimensions")
                self.skip_newlines()
                while not self.check(TokenType.RBRACE) and not self.is_at_end():
                    key = self.v20_key_name()
                    self.consume(TokenType.LBRACKET, "Expected '[' for dimension range")
                    lo = self.expression(); self.consume(TokenType.COMMA, "Expected ',' in dimension range"); hi = self.expression()
                    self.consume(TokenType.RBRACKET, "Expected ']' after dimension range")
                    dimensions[key] = (lo, hi)
                    self.skip_newlines()
                self.consume(TokenType.RBRACE, "Expected '}' after dimensions")
            elif self.match(TokenType.BASELINE):
                self.consume(TokenType.LBRACE, "Expected '{' after baseline")
                self.skip_newlines()
                while not self.check(TokenType.RBRACE) and not self.is_at_end():
                    key = self.v20_key_name(); self.optional_colon(); baseline[key] = self.expression(); self.skip_newlines()
                self.consume(TokenType.RBRACE, "Expected '}' after baseline")
            elif self.match(TokenType.DECAY):
                decay_expr = self.expression()
                decay = getattr(decay_expr, 'value', decay_expr)
                if self.match(TokenType.PER):
                    decay_unit = self.v20_key_name()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected affective state binding")
            else:
                self.error("Expected dimensions, baseline, decay, or bind in affective state")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after affective state")
        return AffectiveStateDef(name=name, dimensions=dimensions, baseline=baseline, decay=float(decay or 0.0), decay_unit=decay_unit, binding=binding, line=token.line, column=token.column)

    def affective_event_stmt(self) -> AffectiveEventStmt:
        token = self.advance(); self.consume(TokenType.EVENT, "Expected 'event' after affective")
        name = self.consume(TokenType.STRING, "Expected affective event name").value if self.check(TokenType.STRING) else self.consume(TokenType.IDENTIFIER, "Expected affective event name").value
        self.consume(TokenType.LBRACE, "Expected '{' after affective event name")
        fields = {}; binding = "emotional_tag"; self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.BIND): binding = self.consume_binding_name("Expected affective event binding")
            else:
                key = self.v20_key_name(); self.optional_colon(); fields[key] = self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after affective event")
        return AffectiveEventStmt(name=name, fields=fields, binding=binding, line=token.line, column=token.column)

    def affective_modulation_stmt(self) -> AffectiveModulationStmt:
        token = self.advance(); self.consume(TokenType.MODULATION, "Expected 'modulation' after affective")
        self.consume(TokenType.LBRACE, "Expected '{' after affective modulation")
        rules=[]; binding="modulation_rules"; self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected modulation binding")
            elif self.check(TokenType.IF):
                rules.append(self.if_stmt())
            elif self.match(TokenType.SUPPRESS, TokenType.ELEVATE, TokenType.INCREASE_CAUTION, TokenType.REDUCE_DEPTH, TokenType.TRIGGER):
                op=str(self.previous().value or self.previous().type.name.lower())
                args=[]
                while not self.check(TokenType.NEWLINE) and not self.check(TokenType.RBRACE) and not self.is_at_end():
                    args.append(self.expression())
                    if not self.match(TokenType.COMMA):
                        if self.check(TokenType.STRING) or self.check(TokenType.IDENTIFIER):
                            continue
                        break
                rules.append(ExprStmt(expr=Literal(value={"op": op, "args": args}, line=token.line, column=token.column)))
            else:
                rules.append(self.statement())
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after affective modulation")
        return AffectiveModulationStmt(rules=rules, binding=binding, line=token.line, column=token.column)

    def affective_resonance_stmt(self) -> AffectiveResonanceStmt:
        token = self.advance(); self.consume(TokenType.RESONANCE, "Expected 'resonance' after affective")
        self.consume(TokenType.WITH, "Expected 'with' after affective resonance")
        if self.match(TokenType.AT):
            target = Literal(value='@' + self.consume(TokenType.IDENTIFIER, "Expected name after @").value, line=token.line, column=token.column)
        else:
            target = self.expression()
        self.consume(TokenType.LBRACE, "Expected '{' after affective resonance target")
        mirror=None; regulate=[]; dampen={}; binding="emotional_bridge"; self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.MIRROR): mirror = self.v20_key_name()
            elif self.match(TokenType.REGULATE): regulate.append(self.v20_key_name())
            elif self.match(TokenType.DAMPEN): key=self.v20_key_name(); dampen[key]=self.expression()
            elif self.match(TokenType.BIND): binding = self.consume_binding_name("Expected affective resonance binding")
            else: self.error("Expected mirror, regulate, dampen, or bind in affective resonance")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after affective resonance")
        return AffectiveResonanceStmt(target=target, mirror=mirror, regulate=regulate, dampen=dampen, binding=binding, line=token.line, column=token.column)

    def somatic_marker_stmt(self) -> SomaticMarkerStmt:
        token = self.advance(); self.consume(TokenType.MARKER, "Expected 'marker' after somatic")
        name = self.consume(TokenType.STRING, "Expected somatic marker name").value if self.check(TokenType.STRING) else self.consume(TokenType.IDENTIFIER, "Expected somatic marker name").value
        self.consume(TokenType.LBRACE, "Expected '{' after somatic marker name")
        fields={}; binding="marker"; self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.BIND): binding=self.consume_binding_name("Expected somatic marker binding")
            else:
                key=self.v20_key_name(); self.optional_colon(); fields[key]=self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after somatic marker")
        return SomaticMarkerStmt(name=name, fields=fields, binding=binding, line=token.line, column=token.column)

    def compile_vm_stmt(self) -> CompileVmStmt:
        token=self.advance(); self.consume(TokenType.VM, "Expected 'vm' after compile")
        source=None; binding="bytecode"
        if self.match(TokenType.LBRACE):
            self.skip_newlines()
            while not self.check(TokenType.RBRACE) and not self.is_at_end():
                if self.match(TokenType.SOURCE): self.optional_colon(); source=self.expression()
                elif self.match(TokenType.BIND): binding=self.consume_binding_name("Expected bytecode binding")
                else: self.error("Expected source or bind in compile vm")
                self.skip_newlines()
            self.consume(TokenType.RBRACE, "Expected '}' after compile vm")
        else:
            source=self.expression()
        return CompileVmStmt(source=source, binding=binding, line=token.line, column=token.column)

    def run_vm_stmt(self) -> RunVmStmt:
        token=self.advance(); self.consume(TokenType.VM, "Expected 'vm' after run")
        source=None; resume_from=None; gas=None; binding="vm_result"
        cognitive_budget=None; checkpoint_label=None; checkpoint_trigger=None
        checkpoint_seen=False
        if self.match(TokenType.LBRACE):
            self.skip_newlines()
            while not self.check(TokenType.RBRACE) and not self.is_at_end():
                if self.match(TokenType.SOURCE):
                    self.optional_colon(); source=self.expression()
                elif self.match(TokenType.RESUME_FROM):
                    self.optional_colon(); resume_from=self.expression()
                elif self.match(TokenType.GAS):
                    self.optional_colon(); gas=self.expression()
                elif self.match(TokenType.COGNITIVE_BUDGET):
                    self.optional_colon()
                    tok = self.consume(TokenType.NUMBER, "Expected numeric cognitive budget")
                    cognitive_budget = int(tok.value)
                elif self.match(TokenType.CHECKPOINT):
                    if checkpoint_seen:
                        self.error("Only one checkpoint clause is allowed in run vm")
                    checkpoint_seen=True
                    label_tok = self.consume(TokenType.STRING, "Expected checkpoint label string")
                    checkpoint_label = label_tok.value
                    if self.match(TokenType.AT_IP):
                        ip_tok = self.consume(TokenType.NUMBER, "Expected instruction pointer after at_ip")
                        checkpoint_trigger = AtIpTrigger(ip=int(ip_tok.value), line=label_tok.line, column=label_tok.column)
                    elif self.match(TokenType.BEFORE_OP):
                        op_tok = self.consume(TokenType.IDENTIFIER, "Expected opcode name after before_op")
                        checkpoint_trigger = BeforeOpTrigger(op=str(op_tok.value), line=op_tok.line, column=op_tok.column)
                    else:
                        self.error("Expected at_ip or before_op after checkpoint label")
                elif self.match(TokenType.BIND):
                    binding=self.consume_binding_name("Expected vm result binding")
                else:
                    self.error("Expected source, resume_from, gas, cognitive_budget, checkpoint, or bind in run vm")
                self.skip_newlines()
            self.consume(TokenType.RBRACE, "Expected '}' after run vm")
        else:
            source=self.expression()
        return RunVmStmt(source=source, resume_from=resume_from, gas=gas, cognitive_budget=cognitive_budget, checkpoint_label=checkpoint_label, checkpoint_trigger=checkpoint_trigger, binding=binding, line=token.line, column=token.column)

    def optional_colon(self):
        self.match(TokenType.COLON)

    def memory_palace_def(self) -> MemoryPalaceDef:
        token = self.advance()  # memory
        self.consume(TokenType.PALACE, "Expected 'palace' after memory")
        name = self.consume(TokenType.STRING, "Expected palace name string").value if self.check(TokenType.STRING) else self.consume(TokenType.IDENTIFIER, "Expected palace name").value
        self.consume(TokenType.LBRACE, "Expected '{' after memory palace name")
        self.skip_newlines()
        rooms = []
        decay_policy = {}
        backend = "sqlite"
        binding = "palace"
        consolidate_during_dream = False
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.ROOMS):
                self.consume(TokenType.LBRACE, "Expected '{' after rooms")
                self.skip_newlines()
                while not self.check(TokenType.RBRACE) and not self.is_at_end():
                    rooms.append(self.room_name_token())
                    self.skip_newlines()
                self.consume(TokenType.RBRACE, "Expected '}' after rooms")
            elif self.match(TokenType.DECAY_POLICY):
                self.consume(TokenType.LBRACE, "Expected '{' after decay_policy")
                self.skip_newlines()
                while not self.check(TokenType.RBRACE) and not self.is_at_end():
                    room = self.room_name_token()
                    self.consume(TokenType.ARROW, "Expected '->' in decay_policy")
                    if self.match(TokenType.NUMBER):
                        val = self.previous().value
                        if self.match(TokenType.IDENTIFIER, TokenType.DAYS, TokenType.EVENTS):
                            decay_policy[room] = f"{val} {self.previous().value or self.previous().type.name.lower()}"
                        else:
                            decay_policy[room] = val
                    elif self.match(TokenType.STRING, TokenType.IDENTIFIER, TokenType.NEVER):
                        decay_policy[room] = str(self.previous().value or self.previous().type.name.lower())
                    else:
                        self.error("Expected decay policy value")
                    self.skip_newlines()
                self.consume(TokenType.RBRACE, "Expected '}' after decay_policy")
            elif self.match(TokenType.CONSOLIDATE):
                self.consume(TokenType.DURING, "Expected 'during' after consolidate")
                self.consume(TokenType.DREAM, "Expected 'dream' after consolidate during")
                consolidate_during_dream = True
            elif self.match(TokenType.BACKEND):
                if self.match(TokenType.IDENTIFIER, TokenType.STRING):
                    backend = str(self.previous().value)
                else:
                    self.error("Expected backend name")
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected binding name")
            else:
                self.error("Expected rooms, decay_policy, consolidate, backend, or bind in memory palace")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after memory palace")
        return MemoryPalaceDef(name=name, rooms=rooms or ["episodic", "semantic", "procedural"], decay_policy=decay_policy, backend=backend, binding=binding, consolidate_during_dream=consolidate_during_dream, line=token.line, column=token.column)

    # --- v2.1.0 Affective Memory parsing helpers ---
    def pad_key_name(self) -> str:
        if self.match(TokenType.VALENCE):
            return "valence"
        if self.match(TokenType.AROUSAL):
            return "arousal"
        if self.match(TokenType.DOMINANCE):
            return "dominance"
        if self.match(TokenType.PLEASURE):
            return "valence"
        if self.match(TokenType.ENERGY_ALIAS):
            return "arousal"
        if self.match(TokenType.CONTROL):
            return "dominance"
        if self.match(TokenType.IDENTIFIER):
            value = str(self.previous().value)
            aliases = {"pleasure": "valence", "energy": "arousal", "control": "dominance"}
            if value in {"valence", "arousal", "dominance"} or value in aliases:
                return aliases.get(value, value)
        self.error("Expected PAD key: valence/arousal/dominance")

    def parse_signed_number_literal(self) -> float:
        sign = 1.0
        if self.match(TokenType.MINUS):
            sign = -1.0
        tok = self.consume(TokenType.NUMBER, "Expected numeric PAD value")
        return sign * float(tok.value)

    def parse_affective_pad_literal(self) -> AffectivePadLiteral:
        token = self.consume(TokenType.LBRACE, "Expected '{' for PAD literal")
        values = {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}
        self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            key = self.pad_key_name()
            self.optional_colon()
            values[key] = self.parse_signed_number_literal()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after PAD literal")
        return AffectivePadLiteral(valence=values["valence"], arousal=values["arousal"], dominance=values["dominance"], line=token.line, column=token.column)

    def parse_decay_expr(self) -> DecayExpr:
        token = self.peek()
        if self.match(TokenType.NEVER):
            return DecayExpr(value=-1, unit="never", original="never", line=token.line, column=token.column)
        if self.match(TokenType.IDENTIFIER) and str(self.previous().value) == "never":
            return DecayExpr(value=-1, unit="never", original="never", line=token.line, column=token.column)
        num = self.consume(TokenType.NUMBER, "Expected decay value or never")
        unit = "events"
        if self.match(TokenType.DAYS):
            unit = "days"
        elif self.match(TokenType.EVENTS):
            unit = "events"
        elif self.match(TokenType.IDENTIFIER):
            unit = str(self.previous().value)
        if unit not in {"days", "events"}:
            self.error("Expected decay unit days or events")
        return DecayExpr(value=int(num.value), unit=unit, original=f"{int(num.value)} {unit}", line=token.line, column=token.column)

    def parse_affective_filter_expr(self) -> AffectiveFilterExpr:
        token = self.peek()
        if self.match(TokenType.TAGGED):
            left = AffectiveFilterExpr(kind="tagged", line=token.line, column=token.column)
        elif self.match(TokenType.UNTAGGED):
            left = AffectiveFilterExpr(kind="untagged", line=token.line, column=token.column)
        else:
            key = self.pad_key_name()
            if self.match(TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE, TokenType.EQ, TokenType.NEQ):
                op = str(self.previous().value or self.previous().type.name.lower())
            else:
                self.error("Expected comparison operator in affective_filter")
            value = self.parse_signed_number_literal()
            left = AffectiveFilterExpr(kind="comparison", left=key, op=op, right=value, line=token.line, column=token.column)
        while self.match(TokenType.AND):
            right = self.parse_affive_filter_expr_alias()
            left = AffectiveFilterExpr(kind="and", left=left, right=right, line=token.line, column=token.column)
        return left

    def parse_affive_filter_expr_alias(self) -> AffectiveFilterExpr:
        return self.parse_affective_filter_expr()

    def imprint_stmt(self) -> ImprintStmt:
        token = self.advance()  # imprint
        self.consume(TokenType.INTO, "Expected 'into' after imprint")
        palace = self.expression()
        room = "episodic"
        if isinstance(palace, MemberAccess):
            room = palace.member
            palace = palace.obj
        self.consume(TokenType.LBRACE, "Expected '{' after imprint target")
        self.skip_newlines()
        fields = {}
        binding = None
        allowed = {TokenType.CONTENT, TokenType.CONFIDENCE, TokenType.SOURCE, TokenType.TRACE_ID, TokenType.REASON, TokenType.SCOPE, TokenType.AFFECTIVE_TAG, TokenType.AFFECTIVE_DECAY, TokenType.IDENTIFIER}
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected imprint binding")
            else:
                key_tok = self.advance()
                if key_tok.type not in allowed:
                    self.error("Expected imprint field")
                key = str(key_tok.value or key_tok.type.name.lower())
                self.optional_colon()
                if key == "affective_tag" and self.check(TokenType.LBRACE):
                    fields[key] = self.parse_affective_pad_literal()
                elif key == "affective_decay":
                    fields[key] = self.parse_decay_expr()
                else:
                    fields[key] = self.expression()
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after imprint")
        return ImprintStmt(palace=palace, room=room, fields=fields, binding=binding, line=token.line, column=token.column)

    def recall_stmt(self) -> RecallStmt:
        token = self.advance()  # recall
        self.consume(TokenType.FROM, "Expected 'from' after recall") if hasattr(TokenType, 'FROM') else None
        palace = self.expression()
        room = "episodic"
        if isinstance(palace, MemberAccess):
            room = palace.member
            palace = palace.obj
        self.consume(TokenType.LBRACE, "Expected '{' after recall target")
        self.skip_newlines()
        query = threshold = limit = None
        binding = "memories"
        affective_filter = None
        affective_sort = None
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.IDENTIFIER) and self.previous().value == "query":
                self.optional_colon(); query = self.expression()
            elif self.match(TokenType.THRESHOLD):
                self.optional_colon(); threshold = self.expression()
            elif self.match(TokenType.LIMIT):
                self.optional_colon(); limit = self.expression()
            elif self.match(TokenType.AFFECTIVE_FILTER):
                affective_filter = self.parse_affective_filter_expr()
            elif self.match(TokenType.AFFECTIVE_SORT):
                key = self.pad_key_name()
                direction = "desc"
                if self.match(TokenType.ASC):
                    direction = "asc"
                elif self.match(TokenType.DESC):
                    direction = "desc"
                elif self.match(TokenType.IDENTIFIER):
                    direction = str(self.previous().value)
                if direction not in {"asc", "desc"}:
                    self.error("Expected asc or desc after affective_sort")
                affective_sort = (key, direction)
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected recall binding")
            else:
                self.error("Expected query, threshold, limit, affective_filter, affective_sort, or bind in recall")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after recall")
        return RecallStmt(palace=palace, room=room, query=query, threshold=threshold, limit=limit, binding=binding, affective_filter=affective_filter, affective_sort=affective_sort, line=token.line, column=token.column)

    def intention_cascade_def(self) -> IntentionCascadeDef:
        token = self.advance()  # intention
        self.consume(TokenType.CASCADE, "Expected 'cascade' after intention")
        name = self.consume(TokenType.STRING, "Expected cascade name string").value if self.check(TokenType.STRING) else self.consume(TokenType.IDENTIFIER, "Expected cascade name").value
        self.consume(TokenType.LBRACE, "Expected '{' after intention cascade")
        self.skip_newlines()
        levels = {}
        binding = "plan"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.MISSION, TokenType.OBJECTIVE, TokenType.TASK, TokenType.ACTION):
                key = self.previous().type.name.lower()
                self.optional_colon(); levels[key] = self.expression()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected cascade binding")
            else:
                self.error("Expected mission, objective, task, action, or bind in intention cascade")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after intention cascade")
        return IntentionCascadeDef(name=name, levels=levels, binding=binding, line=token.line, column=token.column)

    def plan_weave_stmt(self) -> PlanWeaveStmt:
        token = self.advance()  # plan
        self.consume(TokenType.WEAVE, "Expected 'weave' after plan")
        self.consume(TokenType.WITH, "Expected 'with' after plan weave")
        participants = self.participant_list()
        policy_ref = None
        if self.match(TokenType.UNDER):
            policy_ref = self.policy_ref_value()
        self.consume(TokenType.LBRACE, "Expected '{' after plan weave header")
        self.skip_newlines()
        intention = checkpoint_every = timeout = None
        rollback_on = "failure"
        binding = "execution"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.INTENTION):
                self.optional_colon(); intention = self.expression()
            elif self.match(TokenType.CHECKPOINT):
                self.consume(TokenType.EVERY, "Expected 'every' after checkpoint")
                checkpoint_every = self.expression()
                self.match(TokenType.STEPS)
            elif self.match(TokenType.ROLLBACK):
                self.consume(TokenType.ON, "Expected 'on' after rollback")
                if self.match(TokenType.FAILURE, TokenType.IDENTIFIER):
                    rollback_on = str(self.previous().value or self.previous().type.name.lower())
                else:
                    self.error("Expected rollback target")
            elif self.match(TokenType.TIMEOUT):
                timeout = self.expression()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected plan weave binding")
            else:
                self.error("Expected intention, checkpoint, rollback_on, timeout, or bind in plan weave")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after plan weave")
        return PlanWeaveStmt(participants=participants, policy_ref=policy_ref, intention=intention, checkpoint_every=checkpoint_every, rollback_on=rollback_on, timeout=timeout, binding=binding, line=token.line, column=token.column)

    def habit_stmt(self) -> HabitStmt:
        token = self.advance()  # habit
        name = None
        if self.check(TokenType.STRING):
            name = self.advance().value
        elif self.check(TokenType.IDENTIFIER) and self.peek().value not in {"from"}:
            # Optional named habit: habit DeepAnalysis from pattern { ... }
            name = str(self.advance().value)
        if self.match(TokenType.FROM):
            pass
        elif self.check(TokenType.IDENTIFIER) and self.peek().value == "from":
            self.advance()
        self.consume(TokenType.PATTERN, "Expected 'pattern' after habit from")
        self.consume(TokenType.LBRACE, "Expected '{' after habit from pattern")
        self.skip_newlines()

        fields = {}
        binding = "habit_id"
        frequency_op = frequency_val = None
        stability_op = stability_val = None
        promote_to = None
        energy_cost = None
        priority = "medium"
        body = []
        activate_when = []
        suppress_when = []
        fatigue = None

        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected habit binding")
            elif self.match(TokenType.FREQUENCY):
                frequency_op, frequency_val = self.parse_habit_comparison("frequency")
                fields["frequency"] = Literal(value={"op": frequency_op, "value": getattr(frequency_val, "value", None)}, line=token.line, column=token.column)
            elif self.match(TokenType.STABILITY):
                stability_op, stability_val = self.parse_habit_comparison("stability")
                fields["stability"] = Literal(value={"op": stability_op, "value": getattr(stability_val, "value", None)}, line=token.line, column=token.column)
            elif self.match(TokenType.PROMOTE_TO):
                promote_to = self.expression()
                fields["promote_to"] = promote_to
            elif self.match(TokenType.ENERGY_COST):
                energy_cost = self.expression()
                fields["energy_cost"] = energy_cost
            elif self.match(TokenType.PRIORITY):
                priority = self.priority_name()
                fields["priority"] = Literal(value=priority, line=token.line, column=token.column)
            elif self.match(TokenType.BODY):
                body = self.block()
            elif self.match(TokenType.ACTIVATE):
                self.consume(TokenType.WHEN, "Expected 'when' after activate")
                activate_when.append(self.parse_habit_condition())
            elif self.match(TokenType.SUPPRESS):
                self.consume(TokenType.WHEN, "Expected 'when' after suppress")
                suppress_when.append(self.parse_habit_condition())
            elif self.match(TokenType.FATIGUE):
                fatigue = self.parse_fatigue_def()
            else:
                # Backward-compatible generic field parsing from v1.9.
                key_tok = self.advance()
                key = str(key_tok.value or key_tok.type.name.lower())
                if key_tok.type in {TokenType.IDENTIFIER, TokenType.ACTIVATION_CONDITION}:
                    if self.match(TokenType.GT, TokenType.LT, TokenType.GTE, TokenType.LTE, TokenType.EQ):
                        op = self.previous().value or self.previous().type.name.lower()
                        val = self.expression()
                        fields[key] = Literal(value={"op": op, "value": getattr(val, "value", None)}, line=key_tok.line, column=key_tok.column)
                    else:
                        self.optional_colon()
                        fields[key] = self.expression()
                else:
                    self.error("Expected habit field")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after habit")
        return HabitStmt(fields=fields, name=name, frequency_op=frequency_op, frequency_val=frequency_val,
                         stability_op=stability_op, stability_val=stability_val, promote_to=promote_to,
                         energy_cost=energy_cost, priority=priority, body=body,
                         activate_when=activate_when, suppress_when=suppress_when, fatigue=fatigue,
                         binding=binding, line=token.line, column=token.column)

    def parse_habit_comparison(self, label: str):
        if self.match(TokenType.GT, TokenType.LT, TokenType.GTE, TokenType.LTE, TokenType.EQ, TokenType.NEQ):
            op = self.previous().value or self.previous().type.name.lower()
        else:
            self.error(f"Expected comparison operator after {label}")
        val = self.expression()
        return str(op), val

    def priority_name(self) -> str:
        if self.match(TokenType.IDENTIFIER):
            value = str(self.previous().value)
        elif self.match(TokenType.DEFAULT):
            value = "medium"
        else:
            self.error("Expected priority level")
        if value not in {"low", "medium", "high", "critical"}:
            self.error("Priority must be low, medium, high, or critical")
        return value

    def parse_habit_condition(self) -> Node:
        if self.check(TokenType.LBRACE):
            return self.parse_inline_habit_cond()
        # Named threshold reference. Accept keywords used as names too.
        if self.match(TokenType.IDENTIFIER, TokenType.THRESHOLD, TokenType.STABILITY, TokenType.COHERENCE):
            tok = self.previous()
            return ThresholdRef(name=str(tok.value or tok.type.name.lower()), line=tok.line, column=tok.column)
        self.error("Expected threshold name or inline condition block after when")

    def literal_number_value(self, node):
        if isinstance(node, Literal):
            return float(node.value)
        if isinstance(node, UnaryExpr) and node.op == "-" and isinstance(node.operand, Literal):
            return -float(node.operand.value)
        return node

    def parse_inline_habit_cond(self) -> InlineHabitCond:
        token = self.consume(TokenType.LBRACE, "Expected '{' for inline habit condition")
        pad_conditions = []
        context = None
        self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.CONTEXT):
                context = self.consume(TokenType.STRING, "Expected context label string").value
            else:
                if self.match(TokenType.IDENTIFIER) and self.previous().value == "mood":
                    self.consume(TokenType.DOT, "Expected '.' after mood")
                key = self.pad_key_name()
                if self.match(TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE, TokenType.EQ, TokenType.NEQ):
                    op = str(self.previous().value or self.previous().type.name.lower())
                else:
                    self.error("Expected comparison operator in habit condition")
                value_node = self.expression()
                pad_conditions.append((key, op, self.literal_number_value(value_node)))
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after inline habit condition")
        return InlineHabitCond(pad_conditions=pad_conditions, context=context, line=token.line, column=token.column)

    def parse_fatigue_def(self) -> FatigueDef:
        token = self.previous()
        self.consume(TokenType.AFTER, "Expected 'after' after fatigue")
        threshold = int(self.consume(TokenType.NUMBER, "Expected activation count after fatigue after").value)
        self.consume(TokenType.ACTIVATIONS, "Expected 'activations' after fatigue threshold")
        self.consume(TokenType.LBRACE, "Expected '{' after fatigue declaration")
        multiplier = 1.0
        require_rest = 0
        self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.ENERGY_COST_MULTIPLIER):
                multiplier = float(self.consume(TokenType.NUMBER, "Expected multiplier number").value)
            elif self.match(TokenType.REQUIRE_REST):
                require_rest = int(self.consume(TokenType.NUMBER, "Expected rest event count").value)
                self.consume(TokenType.EVENTS, "Expected events after require_rest N")
            else:
                self.error("Expected energy_cost_multiplier or require_rest in fatigue block")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after fatigue block")
        return FatigueDef(threshold=threshold, energy_cost_multiplier=multiplier, require_rest=require_rest, line=token.line, column=token.column)

    def parse_routing_rule(self) -> RoutingRule:
        token = self.consume(TokenType.WHEN, "Expected when in affective_routing")
        if self.match(TokenType.AFFECTIVE_TAG):
            self.consume(TokenType.EQ, "Expected == after affective_tag")
            self.consume(TokenType.NULL, "Expected null after affective_tag ==")
            condition = AffectiveFilterExpr(kind="untagged", line=token.line, column=token.column)
        else:
            condition = self.parse_affective_filter_expr()
        self.consume(TokenType.LBRACE, "Expected '{' after routing condition")
        actions = []
        self.skip_newlines()
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.PROMOTE_TO):
                target = self.room_name_token()
                actions.append(RoutingAction(kind="promote_to", target=target, line=token.line, column=token.column))
            elif self.match(TokenType.KEEP):
                actions.append(RoutingAction(kind="keep", line=token.line, column=token.column))
            elif self.match(TokenType.TAG):
                tag = self.consume(TokenType.STRING, "Expected tag string").value
                actions.append(RoutingAction(kind="tag", tag=tag, line=token.line, column=token.column))
            else:
                self.error("Expected promote_to, keep, or tag in affective_routing rule")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after routing rule")
        return RoutingRule(condition=condition, actions=actions, line=token.line, column=token.column)

    def consolidate_stmt(self) -> ConsolidateStmt:
        token = self.advance()  # consolidate
        palace = self.expression()
        rooms = []
        binding = "consolidation"
        routing = None
        if self.match(TokenType.LBRACE):
            self.skip_newlines()
            while not self.check(TokenType.RBRACE) and not self.is_at_end():
                if self.match(TokenType.ROOMS):
                    rooms = self.parse_string_list()
                elif self.match(TokenType.AFFECTIVE_ROUTING):
                    self.consume(TokenType.LBRACE, "Expected '{' after affective_routing")
                    routing = []
                    self.skip_newlines()
                    while not self.check(TokenType.RBRACE) and not self.is_at_end():
                        routing.append(self.parse_routing_rule())
                        self.skip_newlines()
                    self.consume(TokenType.RBRACE, "Expected '}' after affective_routing")
                elif self.match(TokenType.BIND):
                    binding = self.consume_binding_name("Expected consolidation binding")
                else:
                    self.error("Expected rooms, affective_routing, or bind in consolidate")
                self.skip_newlines()
            self.consume(TokenType.RBRACE, "Expected '}' after consolidate")
        return ConsolidateStmt(palace=palace, rooms=rooms, binding=binding, affective_routing=routing, line=token.line, column=token.column)

    def participant_list(self) -> List[Node]:
        self.consume(TokenType.LBRACKET, "Expected '[' before participant list")
        participants = []
        if not self.check(TokenType.RBRACKET):
            participants.append(self.expression())
            while self.match(TokenType.COMMA):
                participants.append(self.expression())
        self.consume(TokenType.RBRACKET, "Expected ']' after participant list")
        return participants

    def policy_ref_value(self) -> Optional[str]:
        if self.match(TokenType.STRING):
            return str(self.previous().value)
        if self.match(TokenType.IDENTIFIER):
            return str(self.previous().value)
        if self.match(TokenType.POLICY):
            return "policy"
        self.error("Expected policy name")

    def collective_dream_stmt(self) -> CollectiveDreamStmt:
        token = self.advance()  # collective
        self.consume(TokenType.DREAM, "Expected 'dream' after collective")
        self.consume(TokenType.WITH, "Expected 'with' after collective dream")
        participants = self.participant_list()
        policy_ref = None
        if self.match(TokenType.UNDER):
            policy_ref = self.policy_ref_value()
        self.consume(TokenType.LBRACE, "Expected '{' after collective dream header")
        self.skip_newlines()
        scenario = None
        converge_on = None
        depth = "deep"
        timeout = None
        binding = "collective_result"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.SCENARIO):
                scenario = self.expression()
            elif self.match(TokenType.CONVERGE_ON):
                if self.match(TokenType.COLON):
                    pass
                converge_on = self.expression()
            elif self.match(TokenType.DEPTH):
                if self.match(TokenType.IDENTIFIER, TokenType.STRING):
                    depth = str(self.previous().value)
                else:
                    self.error("Expected depth value")
            elif self.match(TokenType.TIMEOUT):
                timeout = self.expression()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected binding name")
            else:
                self.error("Expected scenario, converge_on, depth, timeout, or bind in collective dream")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after collective dream")
        return CollectiveDreamStmt(participants=participants, policy_ref=policy_ref, scenario=scenario,
                                  converge_on=converge_on, depth=depth, timeout=timeout, binding=binding,
                                  line=token.line, column=token.column)

    def distributed_consensus_stmt(self) -> DistributedConsensusStmt:
        token = self.advance()  # distributed
        self.consume(TokenType.CONSENSUS, "Expected 'consensus' after distributed")
        self.consume(TokenType.WITH, "Expected 'with' after distributed consensus")
        participants = self.participant_list()
        self.consume(TokenType.ON, "Expected 'on' after distributed consensus participants")
        topic = self.expression()
        self.consume(TokenType.LBRACE, "Expected '{' after distributed consensus header")
        self.skip_newlines()
        quorum = None
        timeout = None
        policy_ref = None
        binding = "vote"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.QUORUM):
                quorum = self.expression()
            elif self.match(TokenType.TIMEOUT):
                timeout = self.expression()
            elif self.match(TokenType.POLICY):
                policy_ref = self.policy_ref_value()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected binding name")
            else:
                self.error("Expected quorum, timeout, policy, or bind in distributed consensus")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after distributed consensus")
        return DistributedConsensusStmt(participants=participants, topic=topic, quorum=quorum,
                                       timeout=timeout, policy_ref=policy_ref, binding=binding,
                                       line=token.line, column=token.column)

    def swarm_fracture_stmt(self) -> SwarmFractureStmt:
        token = self.advance()  # swarm
        self.consume(TokenType.FRACTURE, "Expected 'fracture' after swarm")
        self.consume(TokenType.WITH, "Expected 'with' after swarm fracture")
        participants = self.participant_list()
        policy_ref = None
        if self.match(TokenType.UNDER):
            policy_ref = self.policy_ref_value()
        self.consume(TokenType.LBRACE, "Expected '{' after swarm fracture header")
        self.skip_newlines()
        scenario = None
        roles = {}
        consensus = "weighted"
        timeout = None
        binding = "swarm_result"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.SCENARIO):
                scenario = self.expression()
            elif self.match(TokenType.ROLES):
                self.consume(TokenType.LBRACE, "Expected '{' after roles")
                self.skip_newlines()
                while not self.check(TokenType.RBRACE) and not self.is_at_end():
                    if self.match(TokenType.IDENTIFIER, TokenType.SELF):
                        actor = str(self.previous().value or "self")
                    else:
                        self.error("Expected actor name in roles block")
                    self.consume(TokenType.ARROW, "Expected '->' in roles block")
                    if self.match(TokenType.IDENTIFIER, TokenType.STRING):
                        role = str(self.previous().value)
                    else:
                        self.error("Expected role name in roles block")
                    roles[actor] = role
                    self.skip_newlines()
                self.consume(TokenType.RBRACE, "Expected '}' after roles")
            elif self.match(TokenType.CONSENSUS):
                if self.match(TokenType.WEIGHTED):
                    consensus = "weighted"
                elif self.match(TokenType.MAJORITY):
                    consensus = "majority"
                elif self.match(TokenType.UNANIMOUS):
                    consensus = "unanimous"
                else:
                    self.error("Expected consensus strategy")
            elif self.match(TokenType.TIMEOUT):
                timeout = self.expression()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected binding name")
            else:
                self.error("Expected scenario, roles, consensus, timeout, or bind in swarm fracture")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after swarm fracture")
        return SwarmFractureStmt(participants=participants, policy_ref=policy_ref, scenario=scenario,
                                roles=roles, consensus_strategy=consensus, timeout=timeout, binding=binding,
                                line=token.line, column=token.column)

    def resonate_stmt(self) -> ResonanceStmt:
        token = self.advance()  # resonate
        self.consume(TokenType.WITH, "Expected 'with' after resonate")
        if self.match(TokenType.AT):
            user_name = self.consume(TokenType.IDENTIFIER, "Expected entity identifier after '@'").value
            target = Literal(value=f"@{user_name}", line=token.line, column=token.column)
        else:
            target = self.expression()
        self.consume(TokenType.LBRACE, "Expected '{' after resonate target")
        self.skip_newlines()
        depth = "deep"
        aspects = []
        window = None
        binding = "resonance"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.DEPTH):
                if self.match(TokenType.IDENTIFIER):
                    depth = str(self.previous().value)
                elif self.match(TokenType.STRING):
                    depth = str(self.previous().value)
                else:
                    self.error("Expected deep or shallow after depth")
            elif self.match(TokenType.ASPECTS):
                aspects = self.parse_string_list()
            elif self.match(TokenType.WINDOW):
                window = self.expression()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected binding name")
            else:
                self.error("Expected depth, aspects, window, or bind in resonate block")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after resonate block")
        return ResonanceStmt(target=target, depth=depth, aspects=aspects, window=window, binding=binding, line=token.line, column=token.column)

    def reflect_on_fractures_stmt(self) -> ReflectOnFracturesStmt:
        token = self.advance()  # reflect
        self.consume(TokenType.ON, "Expected 'on' after reflect")
        fractures = self.consume(TokenType.IDENTIFIER, "Expected 'fractures' after reflect on")
        if fractures.value != "fractures":
            self.error("Expected 'fractures' after reflect on")
        self.consume(TokenType.LBRACE, "Expected '{' after reflect on fractures")
        self.skip_newlines()
        last = None
        filter_condition = None
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.LAST):
                last = self.expression()
                self.match(TokenType.EVENTS)
            elif self.match(TokenType.FILTER):
                filter_condition = self.expression()
            else:
                self.error("Expected last or filter in reflect on fractures")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after reflect on fractures")
        return ReflectOnFracturesStmt(last=last, filter_condition=filter_condition, line=token.line, column=token.column)

    def measure_identity_coherence_stmt(self) -> MeasureIdentityCoherenceStmt:
        token = self.advance()  # measure
        ident = self.consume(TokenType.IDENTIFIER, "Expected identity_coherence after measure")
        if ident.value != "identity_coherence":
            self.error("Expected identity_coherence after measure")
        self.consume(TokenType.LBRACE, "Expected '{' after measure identity_coherence")
        self.skip_newlines()
        window = None
        metrics = []
        binding = "coherence"
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.WINDOW):
                window = self.expression()
            elif self.match(TokenType.METRICS):
                metrics = self.parse_string_list()
            elif self.match(TokenType.BIND):
                binding = self.consume_binding_name("Expected binding name")
            else:
                self.error("Expected window, metrics, or bind in measure block")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after measure block")
        return MeasureIdentityCoherenceStmt(window=window, metrics=metrics, binding=binding, line=token.line, column=token.column)

    def parse_string_list(self) -> List[str]:
        self.consume(TokenType.LBRACKET, "Expected '['")
        items = []
        if not self.check(TokenType.RBRACKET):
            if self.match(TokenType.STRING):
                items.append(self.previous().value)
            elif self.match(TokenType.IDENTIFIER, TokenType.STABILITY, TokenType.CONSENSUS_RATE, TokenType.RESONANCE_DRIFT, TokenType.COHERENCE):
                items.append(str(self.previous().value or self.previous().type.name.lower()))
            else:
                self.error("Expected string or metric name")
            while self.match(TokenType.COMMA):
                if self.match(TokenType.STRING):
                    items.append(self.previous().value)
                elif self.match(TokenType.IDENTIFIER, TokenType.STABILITY, TokenType.CONSENSUS_RATE, TokenType.RESONANCE_DRIFT, TokenType.COHERENCE):
                    items.append(str(self.previous().value or self.previous().type.name.lower()))
                else:
                    self.error("Expected string or metric name after comma")
        self.consume(TokenType.RBRACKET, "Expected ']'")
        return items

    def soulprint_def(self) -> SoulprintDef:
        token = self.advance()  # soulprint
        self.consume(TokenType.LBRACE, "Expected '{' after soulprint")
        self.skip_newlines()
        values = {}
        memory_type = "long-term"
        style = ""
        version = "1.0"
        protected = True
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.VALUES):
                self.consume(TokenType.COLON, "Expected ':' after values")
                self.consume(TokenType.LBRACKET, "Expected '[' after values:")
                if not self.check(TokenType.RBRACKET):
                    while True:
                        key = self.consume(TokenType.IDENTIFIER, "Expected value name in soulprint values").value
                        self.consume(TokenType.COLON, "Expected ':' after soulprint value name")
                        val = self.consume(TokenType.NUMBER, "Expected numeric soulprint value").value
                        values[key] = float(val)
                        if not self.match(TokenType.COMMA):
                            break
                self.consume(TokenType.RBRACKET, "Expected ']' after soulprint values")
            elif self.match(TokenType.MEMORY):
                self.consume(TokenType.COLON, "Expected ':' after memory")
                if self.match(TokenType.STRING):
                    memory_type = self.previous().value
                else:
                    tok = self.advance()
                    if tok.type not in (TokenType.IDENTIFIER, TokenType.SCOPE):
                        self.error("Expected memory type")
                    memory_type = str(tok.value or tok.type.name.lower())
            elif self.match(TokenType.STYLE) or (self.match(TokenType.IDENTIFIER) and self.previous().value == "style"):
                self.consume(TokenType.COLON, "Expected ':' after style")
                style = self.consume(TokenType.STRING, "Expected soulprint style string").value
            elif self.match(TokenType.VERSION) or (self.match(TokenType.IDENTIFIER) and self.previous().value == "version"):
                self.consume(TokenType.COLON, "Expected ':' after version")
                version = self.consume(TokenType.STRING, "Expected soulprint version string").value
            elif self.match(TokenType.PROTECTED) or (self.match(TokenType.IDENTIFIER) and self.previous().value == "protected"):
                self.consume(TokenType.COLON, "Expected ':' after protected")
                if self.match(TokenType.TRUE):
                    protected = True
                elif self.match(TokenType.FALSE):
                    protected = False
                else:
                    protected = bool(self.expression())
            else:
                self.error("Expected values, memory, style, version, or protected in soulprint")
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after soulprint")
        return SoulprintDef(values=values, memory_type=memory_type, style=style, version=version, protected=bool(protected), line=token.line, column=token.column)

    def assert_stmt(self) -> AssertStmt:
        token = self.advance()  # assert
        condition = self.expression()
        message = None
        if self.match(TokenType.COMMA):
            message = self.expression()
        return AssertStmt(condition=condition, message=message, line=token.line, column=token.column)

    def integrate_block(self) -> IntegrateBlock:
        token = self.advance()  # integrate
        dream_result = self.expression()
        self.consume(TokenType.LBRACE, "Expected '{' after integrate target")
        self.skip_newlines()
        body = []
        reason = None
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.REASON):
                reason = self.expression()
            else:
                stmt = self.statement()
                if stmt:
                    body.append(stmt)
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after integrate body")
        on_fail = "rollback"
        self.skip_newlines()
        if self.match(TokenType.ON):
            self.consume(TokenType.FAIL, "Expected 'fail' after 'on'")
            if self.match(TokenType.ROLLBACK):
                on_fail = "rollback"
            elif self.match(TokenType.WARN):
                on_fail = "warn"
            elif self.match(TokenType.HALT):
                on_fail = "halt"
            else:
                self.error("Expected rollback, warn, or halt after on fail")
        elif self.check(TokenType.IDENTIFIER) and self.peek().value == "on_fail":
            self.advance()
            if self.match(TokenType.ROLLBACK):
                on_fail = "rollback"
            elif self.match(TokenType.WARN):
                on_fail = "warn"
            elif self.match(TokenType.HALT):
                on_fail = "halt"
            else:
                self.error("Expected rollback, warn, or halt after on_fail")
        return IntegrateBlock(dream_result=dream_result, body=body, on_fail=on_fail, reason=reason, line=token.line, column=token.column)

    def dream_block(self) -> DreamBlock:
        token = self.advance()  # dream
        self.consume(TokenType.LBRACE, "Expected '{' after dream")
        self.skip_newlines()
        scenario = None
        config = {}
        body = []
        while not self.check(TokenType.RBRACE) and not self.is_at_end():
            if self.match(TokenType.SCENARIO):
                scenario = self.expression()
            elif self.match(TokenType.TEMPERATURE):
                config["temperature"] = self.expression()
            elif self.match(TokenType.DEPTH):
                tok = self.advance()
                if tok.type not in (TokenType.IDENTIFIER, TokenType.STRING):
                    self.error("Expected depth value")
                config["depth"] = Literal(value=str(tok.value), line=tok.line, column=tok.column)
            elif self.match(TokenType.CONSTRAINTS):
                config["constraints"] = self.expression()
            else:
                stmt = self.statement()
                if stmt:
                    body.append(stmt)
            self.skip_newlines()
        self.consume(TokenType.RBRACE, "Expected '}' after dream block")
        integration_clause = None
        # Legacy v1.3 sugar: dream { ... } integrate { ... }
        if self.match(TokenType.INTEGRATE):
            integration_clause = self.block()
        return DreamBlock(scenario=scenario, config=config, body=body, integration_clause=integration_clause, line=token.line, column=token.column)

    def evolve_stmt(self) -> EvolveStmt:
        token = self.advance()  # evolve
        target = self.expression()
        if self.match(TokenType.WHEN):
            condition = self.expression()
        else:
            condition = Literal(value=True, line=token.line, column=token.column)
        delay = None
        delay_unit = "events"
        if self.match(TokenType.AFTER):
            delay = self.expression()
            if self.match(TokenType.EVENTS):
                delay_unit = "events"
            elif self.check(TokenType.IDENTIFIER) and self.peek().value in {"events", "seconds", "calls"}:
                delay_unit = self.advance().value
        policy_ref = None
        if self.match(TokenType.UNDER):
            policy_ref = self.consume(TokenType.IDENTIFIER, "Expected policy name after 'under'").value
        safety_guard = None
        if self.match(TokenType.WITH):
            safety_guard = self.expression()
        mutations = self.block()
        return EvolveStmt(target=target, condition=condition, delay=delay, delay_unit=delay_unit, policy_ref=policy_ref, mutations=mutations, safety_guard=safety_guard, trigger=delay, line=token.line, column=token.column)

    def branch_def(self) -> BranchDef:
        token = self.advance()  # branch
        name = self.consume(TokenType.IDENTIFIER, "Expected branch name").value
        body = self.block()
        return BranchDef(name=name, body=body, line=token.line, column=token.column)

    def memory_access(self) -> MemoryAccess:
        token = self.advance()  # memory
        self.consume(TokenType.DOT, "Expected '.' after memory")
        op_tok = self.consume_name("Expected memory operation")
        operation = op_tok.value if op_tok.value else op_tok.type.name.lower()
        value = None
        if self.match(TokenType.LPAREN):
            if not self.check(TokenType.RPAREN):
                value = self.expression()
            self.consume(TokenType.RPAREN, "Expected ')' after memory operation")
        access = MemoryAccess(name="default", operation=operation, value=value, 
                              line=token.line, column=token.column)
        if operation == "write" and self.check(TokenType.LBRACE):
            fields = self.governed_field_block()
            return GovernedMemoryWrite(value=value, fields=fields, line=token.line, column=token.column)
        if operation == "forget" and self.check(TokenType.LBRACE):
            fields = self.governed_field_block()
            return GovernedMemoryForget(key=value, fields=fields, line=token.line, column=token.column)
        return access

    def list_expr(self) -> ListExpr:
        token = self.advance()  # [
        elements = []
        if not self.check(TokenType.RBRACKET):
            elements.append(self.expression())
            while self.match(TokenType.COMMA):
                elements.append(self.expression())
        self.consume(TokenType.RBRACKET, "Expected ']' after list elements")
        return ListExpr(elements=elements, line=token.line, column=token.column)

    def dict_expr(self) -> DictExpr:
        token = self.advance()  # {
        pairs = []
        if not self.check(TokenType.RBRACE):
            key = self.consume(TokenType.STRING, "Expected string key").value
            self.consume(TokenType.COLON, "Expected ':' after key")
            value = self.expression()
            pairs.append((key, value))
            while self.match(TokenType.COMMA):
                key = self.consume(TokenType.STRING, "Expected string key").value
                self.consume(TokenType.COLON, "Expected ':' after key")
                value = self.expression()
                pairs.append((key, value))
        self.consume(TokenType.RBRACE, "Expected '}' after dict elements")
        return DictExpr(pairs=pairs, line=token.line, column=token.column)
