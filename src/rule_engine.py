import json

class RuleEngine:
    """
    A deterministic rule engine to process validation_rules.json.
    Ensures mathematical and structural checks bypass LLM hallucination.
    """
    
    @staticmethod
    def get_nested_value(d: dict, path: str):
        keys = path.split('.')
        val = d
        try:
            for key in keys:
                val = val[key]
            return val
        except (KeyError, TypeError):
            return None

    @staticmethod
    def evaluate_condition(case_dict: dict, condition: str) -> bool:
        """Lightweight parser for exact logic checks."""
        # Check IS NOT NULL logic
        if " IS NOT NULL" in condition:
            parts = condition.split(" OR ")
            for part in parts:
                path = part.replace(" IS NOT NULL", "").strip()
                if RuleEngine.get_nested_value(case_dict, path) is not None:
                    return True
            return False
            
        # Check > logic
        if " > " in condition:
            path, val_str = condition.split(" > ")
            actual_val = RuleEngine.get_nested_value(case_dict, path.strip())
            return actual_val is not None and float(actual_val) > float(val_str.strip())
            
        return True # Default pass if rule grammar is unrecognized

    @staticmethod
    def run_rules(case_dict: dict, rules: list) -> list:
        issues = []
        for rule in rules:
            condition = rule.get("rule_json", {}).get("condition", "")
            message = rule.get("rule_json", {}).get("message", "Rule violation")
            if not RuleEngine.evaluate_condition(case_dict, condition):
                issues.append(f"[{rule['rule_id']} - {rule['severity'].upper()}]: {message}")
        return issues
        
    @staticmethod
    def calculate_completeness(case_dict: dict) -> tuple:
        """Determines the exact completeness score deterministically."""
        required_keys = ["case_id", "subject", "alert", "institution", "transactions", "narrative"]
        missing = [key for key in required_keys if not case_dict.get(key)]
        score = ((len(required_keys) - len(missing)) / len(required_keys)) * 100
        return score, missing