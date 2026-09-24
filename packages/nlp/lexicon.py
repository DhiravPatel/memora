"""Language resources for the deterministic memory engine.

Everything the engine "knows" about English customer messages lives here as data:
cue phrases, negations, sentiment words, gazetteers and synonyms. Keeping it as data
rather than code means behaviour can be audited, diffed and tuned without touching the
algorithms, and every extraction decision can point at the exact cue that produced it.
"""

from __future__ import annotations

from common.enums import MemoryType

# --------------------------------------------------------------------------- cues
# Cue phrases per memory type. Weight is how strongly the cue implies the type.
# Phrases are matched on a lemmatised, lowercased sentence with word boundaries.

CUES: dict[MemoryType, tuple[tuple[str, float], ...]] = {
    MemoryType.PROBLEM: (
        ("not work", 1.0), ("doesn t work", 1.0), ("does not work", 1.0), ("stop work", 0.95),
        ("stopped work", 0.95), ("broken", 0.95), ("break", 0.6), ("bug", 0.9), ("error", 0.9),
        ("fail", 0.95), ("failure", 0.95), ("crash", 0.95), ("issue", 0.8), ("problem", 0.9),
        ("trouble", 0.85), ("unable to", 0.9), ("can not", 0.8), ("cannot", 0.8), ("couldn t", 0.8),
        ("keep failing", 1.0), ("still failing", 1.0), ("not load", 0.9), ("not sync", 0.9),
        ("time out", 0.85), ("timeout", 0.85), ("stuck", 0.85), ("missing", 0.7),
        ("lost data", 1.0), ("double charge", 1.0), ("charged twice", 1.0), ("wrong", 0.7),
        ("no longer work", 1.0), ("never work", 1.0), ("declined", 0.7), ("rejected", 0.7),
        # A verb that will not do its job is a complaint whatever the verb: "will not
        # connect", "won't open", "does not save".
        ("not connect", 0.9), ("not open", 0.8), ("not save", 0.85), ("not send", 0.8),
        ("not log in", 0.85), ("not start", 0.8), ("not let", 0.7), ("keep reject", 0.9),
        ("not be able to", 0.85), ("timing out", 0.85),
        # Symptoms: how people describe a fault without naming one.
        ("loops back", 0.85), ("stuck in a loop", 0.9), ("freezes", 0.85), ("frozen", 0.85),
        ("keeps hanging", 0.85), ("stalls", 0.75), ("blank page", 0.9), ("blank screen", 0.9),
        ("white screen", 0.9), ("glitch", 0.85), ("outage", 1.0), ("is down", 0.85), ("laggy", 0.8),
        ("keeps spinning", 0.85), ("kicked out", 0.85), ("logs me out", 0.8), ("nothing happens", 0.85),
        ("does nothing", 0.8), ("disappeared", 0.8), ("vanished", 0.85), ("out of sync", 0.9),
        ("mismatch", 0.8), ("incorrect", 0.75), ("inaccurate", 0.8), ("takes forever", 0.9),
        ("slow to load", 0.9), ("bounced", 0.6), ("is blank", 0.85),
    ),
    MemoryType.PREFERENCE: (
        ("prefer", 1.0), ("rather", 0.8), ("instead of", 0.6), ("please contact", 0.95),
        ("contact me", 0.95), ("reach me", 0.95), ("email me", 0.9), ("call me", 0.9),
        ("don t email", 1.0), ("do not email", 1.0), ("stop sending", 0.9), ("unsubscribe", 0.9),
        ("like to have", 0.6), ("usually use", 0.7), ("always use", 0.8), ("favourite", 0.8),
        ("favorite", 0.8), ("opt out", 0.9), ("opt in", 0.8), ("notify me", 0.8),
    ),
    MemoryType.GOAL: (
        ("want to", 0.9), ("would like to", 0.9), ("try to", 0.75), ("trying to", 0.8),
        ("plan to", 0.95), ("planning to", 0.95), ("hope to", 0.85), ("need to", 0.8),
        ("looking to", 0.9), ("our goal", 1.0), ("we aim", 0.95), ("so that we can", 0.8),
        ("in order to", 0.7), ("by the end of", 0.6), ("launch", 0.6), ("scale to", 0.85),
        ("roll out", 0.7),
    ),
    MemoryType.INTENT: (
        ("thinking about", 0.85), ("considering", 0.9), ("evaluating", 0.9), ("comparing", 0.85),
        ("might cancel", 1.0), ("may cancel", 1.0), ("about to", 0.85), ("switch to", 0.9),
        ("move to", 0.8), ("look at alternative", 1.0), ("competitor", 0.85), ("trial", 0.6),
        ("demo", 0.6), ("quote", 0.7), ("renewal", 0.7),
    ),
    MemoryType.SUBSCRIPTION: (
        ("upgrade", 0.95), ("downgrade", 1.0), ("cancel", 0.95), ("subscription", 0.9),
        ("plan", 0.7), ("billing", 0.85), ("invoice", 0.85), ("refund", 0.95), ("payment", 0.8),
        ("charge", 0.7), ("renew", 0.8), ("seat", 0.6), ("price", 0.7), ("discount", 0.7),
    ),
    MemoryType.FEEDBACK: (
        ("love", 0.9), ("great", 0.7), ("awesome", 0.8), ("excellent", 0.8), ("terrible", 0.9),
        ("awful", 0.9), ("disappointed", 0.95), ("frustrating", 0.95), ("confusing", 0.85),
        ("hard to use", 0.9), ("easy to use", 0.85), ("suggestion", 0.9), ("feature request", 1.0),
        ("would be nice", 0.85), ("wish", 0.8), ("feedback", 0.9), ("rating", 0.7),
    ),
    MemoryType.BEHAVIOR: (
        ("use", 0.5), ("using", 0.55), ("used", 0.5), ("set up", 0.6), ("configure", 0.6),
        ("connect", 0.6), ("import", 0.6), ("export", 0.6), ("create", 0.5), ("enable", 0.55),
        ("login", 0.4), ("log in", 0.4), ("invite", 0.6),
    ),
    MemoryType.RELATIONSHIP: (
        ("my team", 0.8), ("our team", 0.8), ("colleague", 0.85), ("my manager", 0.9),
        ("work at", 0.85), ("work for", 0.85), ("agency", 0.7), ("client", 0.7),
        ("our company", 0.8), ("partner", 0.7),
    ),
}

# Cues that say a previously reported problem is over.
RESOLUTION_CUES: tuple[str, ...] = (
    "now work", "works now", "working now", "resolve", "resolved", "fixed", "sorted",
    "back to normal", "no longer an issue", "solved", "all good", "up and running",
)

# --------------------------------------------------------------------- negation
NEGATIONS: frozenset[str] = frozenset(
    {
        "not", "no", "never", "none", "nothing", "neither", "nor", "cannot", "cant", "can t",
        "dont", "doesnt", "didnt", "isnt", "arent", "wasnt", "werent", "wont", "wouldnt",
        "shouldnt", "couldnt", "hasnt", "havent", "hadnt", "without", "unable",
    }
)

# Negation stops at these words: "it does not work, but billing is fine".
NEGATION_STOPWORDS: frozenset[str] = frozenset({"but", "however", "although", "though", "yet", "still"})

NEGATION_SCOPE = 4  # tokens after the negation that are considered negated

# ------------------------------------------------------------------- sentiment
NEGATIVE_WORDS: dict[str, float] = {
    "angry": 1.0, "furious": 1.0, "frustrated": 0.9, "frustrating": 0.9, "annoyed": 0.8,
    "annoying": 0.8, "disappointed": 0.85, "unhappy": 0.8, "upset": 0.8, "terrible": 0.9,
    "awful": 0.9, "horrible": 0.9, "useless": 0.9, "broken": 0.8, "worst": 0.95, "bad": 0.6,
    "slow": 0.5, "confusing": 0.6, "difficult": 0.5, "hard": 0.4, "fail": 0.7, "failing": 0.8,
    "failed": 0.8, "error": 0.6, "bug": 0.6, "crash": 0.8, "wrong": 0.6, "lost": 0.7,
    "unacceptable": 1.0, "ridiculous": 0.9, "waste": 0.8, "disaster": 0.95,
}

POSITIVE_WORDS: dict[str, float] = {
    "love": 0.9, "loving": 0.9, "great": 0.7, "awesome": 0.85, "excellent": 0.85,
    "fantastic": 0.9, "perfect": 0.85, "happy": 0.8, "glad": 0.6, "helpful": 0.7,
    "easy": 0.6, "fast": 0.5, "smooth": 0.6, "reliable": 0.7, "amazing": 0.9, "thanks": 0.4,
    "appreciate": 0.6, "works": 0.5, "working": 0.5, "solved": 0.6, "resolved": 0.6,
    # Deliberately included so that *negated* forms ("does not work", "cannot connect")
    # register as complaints rather than as neutral text.
    "work": 0.45, "connect": 0.4, "load": 0.35, "sync": 0.4, "open": 0.3, "save": 0.35,
    "receive": 0.35, "responsive": 0.6, "stable": 0.7, "accurate": 0.6,
}

URGENCY_WORDS: dict[str, float] = {
    "urgent": 1.0, "urgently": 1.0, "asap": 1.0, "immediately": 0.95, "critical": 1.0,
    "blocker": 1.0, "blocking": 0.9, "production": 0.7, "down": 0.8, "outage": 1.0,
    "emergency": 1.0, "today": 0.5, "now": 0.4, "still": 0.4, "again": 0.5, "third time": 0.8,
    "multiple times": 0.8, "repeatedly": 0.8, "deadline": 0.7,
}

CHURN_WORDS: dict[str, float] = {
    "cancel": 0.9, "cancelling": 1.0, "canceling": 1.0, "cancellation": 1.0, "refund": 0.8,
    "downgrade": 0.85, "leave": 0.6, "leaving": 0.8, "switch": 0.7, "switching": 0.85,
    "competitor": 0.8, "alternative": 0.6, "churn": 1.0, "terminate": 0.9, "unsubscribe": 0.7,
    "not renewing": 1.0, "wont renew": 1.0,
}

INTENSIFIERS: dict[str, float] = {
    "very": 1.3, "really": 1.25, "extremely": 1.5, "incredibly": 1.4, "so": 1.15,
    "totally": 1.3, "completely": 1.35, "absolutely": 1.4, "quite": 1.1, "super": 1.25,
}

DIMINISHERS: dict[str, float] = {
    "slightly": 0.6, "somewhat": 0.7, "a bit": 0.7, "a little": 0.7, "kind of": 0.7,
    "sort of": 0.7, "maybe": 0.8, "possibly": 0.8,
}

# ------------------------------------------------------------------ gazetteers
INTEGRATIONS: dict[str, str] = {
    "shopify": "Shopify", "stripe": "Stripe", "slack": "Slack", "hubspot": "HubSpot",
    "zendesk": "Zendesk", "intercom": "Intercom", "posthog": "PostHog",
    "salesforce": "Salesforce", "quickbooks": "QuickBooks", "woocommerce": "WooCommerce",
    "square": "Square", "mailchimp": "Mailchimp", "google analytics": "Google Analytics",
    "google sheets": "Google Sheets", "zapier": "Zapier", "xero": "Xero", "netsuite": "NetSuite",
    "magento": "Magento", "bigcommerce": "BigCommerce", "amazon": "Amazon", "ebay": "eBay",
    "paypal": "PayPal", "razorpay": "Razorpay", "twilio": "Twilio", "sendgrid": "SendGrid",
    "segment": "Segment", "snowflake": "Snowflake", "notion": "Notion", "jira": "Jira",
    "github": "GitHub", "gitlab": "GitLab", "teams": "Microsoft Teams", "outlook": "Outlook",
    "gmail": "Gmail", "whatsapp": "WhatsApp", "swiggy": "Swiggy", "zomato": "Zomato",
}

PLANS: dict[str, str] = {
    "free": "Free", "trial": "Trial", "starter": "Starter", "basic": "Basic", "lite": "Lite",
    "standard": "Standard", "plus": "Plus", "pro": "Pro", "professional": "Professional",
    "premium": "Premium", "business": "Business", "growth": "Growth", "scale": "Scale",
    "team": "Team", "enterprise": "Enterprise",
}

# Contact channels, used for preference-conflict detection.
CHANNELS: dict[str, str] = {
    "email": "email", "e mail": "email", "mail": "email", "whatsapp": "WhatsApp",
    "phone": "phone", "call": "phone", "sms": "SMS", "text message": "SMS", "slack": "Slack",
    "chat": "chat", "in app": "in-app", "telegram": "Telegram", "teams": "Microsoft Teams",
}

ROLES: frozenset[str] = frozenset(
    {"founder", "ceo", "cto", "coo", "manager", "owner", "admin", "developer", "engineer",
     "marketer", "analyst", "accountant", "agent", "director", "head", "lead"}
)

# Words that look like proper nouns but are not entities.
PROPER_NOUN_STOPLIST: frozenset[str] = frozenset(
    {"i", "we", "you", "they", "hi", "hello", "hey", "thanks", "thank", "please", "the", "a",
     "an", "it", "this", "that", "our", "my", "your", "their", "monday", "tuesday", "wednesday",
     "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may",
     "june", "july", "august", "september", "october", "november", "december", "customer",
     "support", "team", "regards", "sincerely", "best"}
)

# ------------------------------------------------------------------- synonyms
# Used to expand a query so "billing" also matches "invoice" and "charge".
SYNONYMS: dict[str, tuple[str, ...]] = {
    "problem": ("issue", "error", "bug", "failure", "fault", "trouble"),
    "issue": ("problem", "error", "bug", "failure"),
    "broken": ("failing", "not working", "down"),
    "downgrade": ("downgraded", "plan change", "cancel"),
    "upgrade": ("upgraded", "plan change"),
    "billing": ("invoice", "payment", "charge", "subscription", "price"),
    "invoice": ("billing", "payment", "charge"),
    "churn": ("cancel", "leave", "downgrade", "not renewing"),
    "integration": ("connection", "sync", "connector"),
    "sync": ("synchronise", "synchronize", "integration"),
    "support": ("ticket", "help", "contacted"),
    "preference": ("prefers", "likes", "contact"),
    "goal": ("objective", "plan", "target"),
    "onboarding": ("setup", "getting started", "install"),
    "refund": ("money back", "chargeback"),
}

# Common misspellings seen in support text.
SPELLING_FIXES: dict[str, str] = {
    "shopfy": "shopify", "shoppify": "shopify", "stipe": "stripe", "stripee": "stripe",
    "recieve": "receive", "occured": "occurred", "seperate": "separate", "adress": "address",
    "acount": "account", "accont": "account", "billling": "billing", "invocie": "invoice",
    "cancle": "cancel", "canel": "cancel", "payement": "payment", "subcription": "subscription",
    "intergration": "integration", "intergrations": "integrations", "dont": "don't",
    "doesnt": "doesn't", "cant": "can't", "wont": "won't", "isnt": "isn't",
}

# Phrases that carry no durable information.
FILLER_SENTENCES: tuple[str, ...] = (
    "hi", "hello", "hey", "thanks", "thank you", "thanks in advance", "regards", "best regards",
    "kind regards", "please advise", "any update", "any updates", "following up", "bump",
    "ok", "okay", "sure", "yes", "no", "cheers", "sorry", "hope you are well", "good morning",
    "good afternoon", "good evening", "please help", "help", "?", "!",
)

# First person → third person rewriting. Order matters: longest first.
PRONOUN_REWRITES: tuple[tuple[str, str], ...] = (
    ("i've", "the customer has"), ("i have", "the customer has"), ("i'm", "the customer is"),
    ("i am", "the customer is"), ("i'll", "the customer will"), ("i will", "the customer will"),
    ("i'd like", "the customer would like"), ("i would", "the customer would"),
    ("i can't", "the customer cannot"), ("i cannot", "the customer cannot"),
    ("i can not", "the customer cannot"), ("i can", "the customer can"),
    ("i don't", "the customer does not"), ("i do not", "the customer does not"),
    ("i didn't", "the customer did not"), ("i did not", "the customer did not"),
    ("i was", "the customer was"), ("i were", "the customer was"),
    ("we've", "the customer has"), ("we have", "the customer has"),
    ("we're", "the customer is"), ("we are", "the customer is"),
    ("we'll", "the customer will"), ("we will", "the customer will"),
    ("we can't", "the customer cannot"), ("we cannot", "the customer cannot"),
    ("we don't", "the customer does not"), ("we do not", "the customer does not"),
    ("we didn't", "the customer did not"), ("we did not", "the customer did not"),
    ("we were", "the customer was"), ("we was", "the customer was"),
    ("my ", "the customer's "), ("our ", "the customer's "), ("mine", "the customer's"),
    ("ours", "the customer's"),
)

# Verbs that need an -s when the subject becomes "the customer".
VERB_AGREEMENT: dict[str, str] = {
    "want": "wants", "need": "needs", "like": "likes", "prefer": "prefers", "use": "uses",
    "try": "tries", "think": "thinks", "feel": "feels", "hope": "hopes", "plan": "plans",
    "love": "loves", "hate": "hates", "get": "gets", "see": "sees", "keep": "keeps",
    "run": "runs", "pay": "pays", "expect": "expects", "require": "requires", "wish": "wishes",
    "find": "finds", "know": "knows", "work": "works", "look": "looks", "wait": "waits",
}

# Standalone first-person subjects that trigger verb agreement after rewriting.
SUBJECT_PRONOUNS: tuple[str, ...] = ("i", "we")

# Contractions are expanded before cue matching so "doesn't work" matches "does not work".
CONTRACTIONS: dict[str, str] = {
    "can't": "cannot", "cant": "cannot", "won't": "will not", "wont": "will not",
    "n't": " not", "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not", "weren't": "were not",
    "haven't": "have not", "hasn't": "has not", "hadn't": "had not",
    "couldn't": "could not", "shouldn't": "should not", "wouldn't": "would not",
    "i'm": "i am", "i've": "i have", "i'll": "i will", "i'd": "i would",
    "we're": "we are", "we've": "we have", "we'll": "we will", "we'd": "we would",
    "you're": "you are", "you've": "you have", "it's": "it is", "that's": "that is",
    "there's": "there is", "they're": "they are", "they've": "they have",
    "let's": "let us", "what's": "what is", "here's": "here is", "who's": "who is",
}
