"""
skills_db.py — curated controlled vocabulary for skill/tool detection.

Why a dictionary instead of pure NLP: for resumes/JDs, a hand-curated skills
list is more precise than TF-IDF guessing. We normalize aliases (js -> javascript)
so "Node JS", "NodeJS", and "Node.js" all map to one canonical token.

Edit these lists freely to match your field.
"""

# canonical_skill -> list of aliases (all lowercase, matched as whole words)
SKILLS = {
    # languages
    "python": ["python", "py"],
    "java": ["java"],
    "javascript": ["javascript", "js", "ecmascript"],
    "typescript": ["typescript", "ts"],
    "c++": ["c++", "cpp"],
    "c": ["c language", "ansi c"],
    "c#": ["c#", "csharp"],
    "go": ["golang", "go lang"],
    "rust": ["rust"],
    "ruby": ["ruby"],
    "php": ["php"],
    "scala": ["scala"],
    "kotlin": ["kotlin"],
    "swift": ["swift"],
    "sql": ["sql"],
    "bash": ["bash", "shell scripting"],
    # web / backend
    "flask": ["flask"],
    "django": ["django"],
    "fastapi": ["fastapi"],
    "node.js": ["node.js", "nodejs", "node js", "node"],
    "express": ["express", "express.js", "expressjs"],
    "react": ["react", "react.js", "reactjs"],
    "angular": ["angular"],
    "vue": ["vue", "vue.js"],
    "spring": ["spring", "spring boot", "springboot"],
    "rest apis": ["rest api", "rest apis", "restful", "rest", "restful api", "restful apis"],
    "graphql": ["graphql"],
    "grpc": ["grpc"],
    "microservices": ["microservices", "microservice"],
    "html5": ["html", "html5"],
    "css3": ["css", "css3"],
    "json": ["json"],
    "chart.js": ["chart.js", "chartjs", "chart js"],
    # databases
    "postgresql": ["postgresql", "postgres", "psql"],
    "mysql": ["mysql"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "sqlite": ["sqlite"],
    "dynamodb": ["dynamodb"],
    "elasticsearch": ["elasticsearch", "elastic search"],
    # cloud / devops
    "aws": ["aws", "amazon web services"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "azure": ["azure", "microsoft azure"],
    "docker": ["docker"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform"],
    "ci/cd": ["ci/cd", "cicd", "continuous integration", "continuous deployment"],
    "kafka": ["kafka"],
    "rabbitmq": ["rabbitmq"],
    # ml / ai
    "tensorflow": ["tensorflow"],
    "keras": ["keras"],
    "pytorch": ["pytorch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "nlp": ["nlp", "natural language processing"],
    "computer vision": ["computer vision", "cnn", "convolutional"],
    "llm": ["llm", "large language model", "gemini", "gpt", "claude api"],
    "google gemini api": ["google gemini api", "gemini api"],
    "sam": ["sam", "segment anything model"],
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    # concepts
    "data structures": ["data structures", "dsa", "algorithms"],
    "oop": ["oop", "object oriented", "object-oriented"],
    "system design": ["system design", "distributed systems"],
    "unit testing": ["unit testing", "unit tests", "pytest", "junit", "jest"],
    "agile": ["agile", "scrum"],
    "api design": ["api design"],
    "client-server": ["client-server", "client server"],
    "oauth": ["oauth", "oauth2", "oauth-based"],
    "google calendar api": ["google calendar api"],
}

# Tools are tracked separately (the "tools_match" dimension)
TOOLS = {
    "git": ["git", "gitlab", "version control"],
    "github": ["github"],
    "postman": ["postman"],
    "vs code": ["vs code", "vscode", "visual studio code"],
    "jira": ["jira"],
    "linux": ["linux", "unix"],
    "jenkins": ["jenkins"],
    "figma": ["figma"],
}

# Domain keywords -> canonical domain
DOMAINS = {
    "ai/ml": ["machine learning", "deep learning", "ai", "ml", "nlp", "computer vision", "data science"],
    "fintech": ["fintech", "payments", "banking", "trading", "financial"],
    "full-stack": ["full stack", "full-stack", "frontend", "backend", "web application"],
    "cloud": ["cloud", "infrastructure", "devops", "platform"],
    "climate": ["climate", "weather", "environmental", "sustainability"],
    "healthcare": ["healthcare", "health", "medical", "clinical"],
    "ecommerce": ["ecommerce", "e-commerce", "retail", "marketplace"],
}


def _build_reverse(mapping):
    rev = {}
    for canonical, aliases in mapping.items():
        for a in aliases:
            rev[a] = canonical
    return rev


SKILL_ALIASES = _build_reverse(SKILLS)
TOOL_ALIASES = _build_reverse(TOOLS)
