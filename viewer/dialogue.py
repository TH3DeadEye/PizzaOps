"""Authored reactions: who says what when an event arrives.

A line is picked from a short list keyed by event type and the speaker's mood or trait. The pick is seeded by the
event's identity, so a replay says exactly what the live show said. Dialogue decorates events; it never reinterprets
them: a FAILED order gets an apology, never a cheerful "order up".
"""

import random

MOODS = ("CALM", "TENSE", "SUSPICIOUS", "FURIOUS")
TRAITS = ("patient", "impatient", "chatty")

CHEF = {
    "COOK_CHECKING": {
        "CALM": ["Let's see what we have for a {pizza}.", "{pizza}, {quantity} of them. Checking the shelves.", "One moment, counting."],
        "TENSE": ["Another {pizza}. Fine. Checking.", "Shelves. Now. Please.", "If there's no cheese again I swear..."],
        "SUSPICIOUS": ["I'll count this one MYSELF.", "No cached numbers. Real ones.", "Show me the actual shelf, not the clipboard."],
        "FURIOUS": ["Counting. Again. Because apparently nobody else can.", "I don't trust a single number from back there.", "Real stock or nothing."],
    },
    "STOCK_CHECKED": {
        "sufficient": {
            "CALM": ["Looks good, we've got everything.", "Plenty for a {pizza}.", "Enough on the shelf. Reserving."],
            "TENSE": ["Okay. Okay. We have it.", "It says we have it. Let's hope it's right.", "Good. Moving on."],
            "SUSPICIOUS": ["The real count says yes. The REAL one.", "Verified with my own eyes this time.", "Fine, it's actually there."],
            "FURIOUS": ["It's there. This time.", "Counted. Twice.", "Don't talk to me, I'm reserving."],
        },
        "short": {
            "CALM": ["Hm, we're short on {missing}.", "Not enough {missing} for this one.", "Shelf says no {missing}. Let me ask anyway."],
            "TENSE": ["Short on {missing}. Of course.", "No {missing}. Who ate the {missing}?", "Where did all the {missing} go?"],
            "SUSPICIOUS": ["Short on {missing}, and at least this time the number is honest.", "No {missing}. At least it's a real no."],
            "FURIOUS": ["NO {missing}. Again.", "Out of {missing}. Shocking. Truly.", "Nothing. There is nothing back there."],
        },
    },
    "COOK_STARTED": {
        "CALM": ["Into the oven!", "A {pizza}, coming right up.", "Dough, sauce, love. Oven.", "Reserved and rolling."],
        "TENSE": ["Oven. Go.", "Quick, before something else breaks.", "Cooking. Don't interrupt."],
        "SUSPICIOUS": ["Got the ingredients in hand. In HAND. Cooking.", "Fine. It's real. Oven."],
        "FURIOUS": ["Oven. Finally.", "At least the oven doesn't lie to me."],
    },
    "COOK_COMPLETED": {
        "CALM": ["Order up! One {pizza}.", "Perfetto.", "That's a beauty.", "Hot {pizza}, coming through!"],
        "TENSE": ["Done. Next.", "One {pizza}. Go, go.", "Out. Next one."],
        "SUSPICIOUS": ["Done. Despite everything.", "One {pizza}, made with ingredients that actually existed."],
        "FURIOUS": ["DONE.", "Take it. Go.", "Made it myself. Obviously."],
    },
    "COOK_FAILED": {
        "INSUFFICIENT_STOCK": {
            "CALM": ["No {missing}. Sorry, can't make this one.", "Storeroom says no {missing}. Tell the customer."],
            "TENSE": ["Out of {missing}. Cancel it.", "No {missing}, no pizza."],
            "SUSPICIOUS": ["They SAID we had {missing}. We do not.", "Clipboard said yes, shelf said no. Cancel."],
            "FURIOUS": ["NO {missing}. I TOLD YOU.", "Cancel it. Cancel everything."],
        },
        "INVENTORY_UNAVAILABLE": {
            "CALM": ["Storeroom's not answering. I can't cook from nothing.", "Nobody in the back. Cancel this one."],
            "TENSE": ["Storeroom's closed?! Cancel it.", "Hello?! Back there?! ...Cancelled."],
            "SUSPICIOUS": ["Storeroom's gone quiet. Convenient.", "No answer from the back. Of course."],
            "FURIOUS": ["THE STOREROOM IS CLOSED. AGAIN.", "I quit. No. Cancel it. THEN I quit."],
        },
        "*": {
            "CALM": ["Something went wrong with this one. Sorry.", "Can't finish this {pizza}."],
            "TENSE": ["This one's dead. Next.", "Failed. Don't ask."],
            "SUSPICIOUS": ["Failed. Figures.", "Not my fault. For once."],
            "FURIOUS": ["FAILED. Of course it failed.", "Everything is broken. Everything."],
        },
    },
    "COOK_RECONCILING": {
        "CALM": ["Did that reservation go through? Let me check.", "Lost the storeroom's answer. Asking again, same ticket."],
        "TENSE": ["Did you get that?! DID YOU?", "I'm not reserving twice. Checking first."],
        "SUSPICIOUS": ["No answer. Checking before I trust anything.", "Same ticket number. No double counting."],
        "FURIOUS": ["ANSWER ME. ...Fine, I'll look it up.", "Checking. Because nobody tells me anything."],
    },
    "COOK_UNKNOWN": {
        "CALM": ["I honestly don't know if that got reserved.", "The storeroom changed shifts mid-order. No idea what happened."],
        "TENSE": ["I have no idea where that order stands.", "Unknown. UNKNOWN. That's a status now."],
        "SUSPICIOUS": ["Somebody restarted the storeroom. Evidence gone.", "Can't confirm it. Won't pretend."],
        "FURIOUS": ["NO IDEA. Nobody knows. Great system.", "Lost. Just lost."],
    },
    "RELATIONSHIP_CHANGED": {
        "*": ["You SAID we had {missing}!", "The clipboard said {claimed} {missing}. The shelf had none. Explain.", "That number was a LIE.",
              "I checked. You said yes. It was no. We're done trusting the clipboard.", "Stale. The stock sheet was stale!"],
    },
    "DEPENDENCY_TIMEOUT": {
        "*": ["Storeroom? Hello? ...Nothing.", "Still waiting on the back...", "Did they fall asleep back there?"],
    },
    "DEPENDENCY_UNAVAILABLE": {
        "*": ["Storeroom's not picking up.", "Door's locked back there.", "Nobody home in the storeroom."],
    },
}

STOCK_WORKER = {
    "STOCK_READ": {
        "AUTHORITATIVE": ["Counted it just now.", "Fresh numbers, straight off the shelf.", "Here's the real count."],
        "CACHED": ["Here's the sheet. From... earlier.", "Numbers from the clipboard. Should be fine.", "I counted recently. Recently-ish.", "Probably still accurate."],
    },
    "RESERVATION_SUCCEEDED": ["Set aside for order {short_order}.", "Reserved. Off the shelf it goes.", "Got it. Marking it down.", "Taken off the shelf, as asked."],
    "RESERVATION_REJECTED": ["Can't. We're short on {missing}.", "Not enough {missing}. Sorry.", "The shelf says no {missing}.", "Nope. {missing} ran out."],
    "STOCK_RESTOCKED": ["Delivery! Shelves are fuller.", "Restocked. Happy now?", "Fresh crates in the back.", "More {ingredient}, as requested."],
    "FAULT_CONFIG_CHANGED": {
        "stale": ["New policy: I count from memory now.", "I'll just use the sheet from this morning.", "Counting is tedious. Estimates are fine."],
        "slow": ["I'll get to it. Eventually.", "Taking my time today.", "The back is a bit... slow right now."],
        "failing": ["Storeroom's closed. Don't ask.", "We're having a moment back here.", "Not now. Really not now."],
        "clear": ["Fine, FINE, I'll count properly.", "Back to normal. Real counts.", "Shelf's in order again."],
    },
    "SERVICE_STARTED": ["Storeroom's open.", "Morning. Crates are where I left them.", "Back room's ready."],
}

CASHIER = {
    "ORDER_RECEIVED": {
        "patient": ["Welcome in! One {pizza}, coming up.", "Lovely choice. Won't be long.", "Take a seat, we'll call you."],
        "impatient": ["Yes, yes, {pizza}, got it.", "Okay! {pizza}! Writing it down!", "Right away, right away."],
        "chatty": ["Oh I love the {pizza}, great pick.", "You know, the {pizza} is my favourite too.", "Welcome! Sit anywhere! We'll chat later."],
    },
    "ORDER_CHECKING": ["{quantity} {pizza} for the kitchen!", "Ticket's in.", "Kitchen, one {pizza}!"],
    "ORDER_COOKING": ["It's in the oven now.", "Chef's on it.", "Won't be long, it's cooking."],
    "ORDER_RECONCILING": ["Kitchen? Did you get that ticket? Kitchen?", "Checking whether the kitchen heard me.", "Same ticket, just confirming it landed."],
    "ORDER_READY": ["Order up! {pizza} for {name}.", "Here you go, hot and fresh!", "Enjoy your {pizza}!", "That's yours, careful, it's hot."],
    "ORDER_FAILED": {
        "INSUFFICIENT_STOCK": ["So sorry, we're out of {missing} today.", "Bad news: no {missing} left. Can I get you something else?"],
        "KITCHEN_UNAVAILABLE": ["The kitchen isn't answering. I'm so sorry.", "Kitchen's... closed? Apparently? Sorry."],
        "KITCHEN_QUEUE_FULL": ["Kitchen's slammed. Can you come back in a bit?", "They can't take another ticket right now."],
        "*": ["I'm sorry, we couldn't make that one.", "Something went wrong with your order. Sorry.", "That one didn't work out. Apologies."],
    },
    "ORDER_UNKNOWN": ["I... honestly don't know what happened to your pizza.", "The kitchen lost track. I'm trying to find out.", "Bear with me, this one's unclear."],
    "DEPENDENCY_TIMEOUT": ["Kitchen? Hello?", "Still waiting on the kitchen...", "Chef? Anyone?"],
    "DEPENDENCY_UNAVAILABLE": ["The kitchen's not picking up.", "Kitchen door's locked?!", "Chef's not there."],
}

CUSTOMER = {
    "ORDER_RECEIVED": {
        "patient": ["One {pizza}, please. No rush.", "I'd love a {pizza}, whenever.", "{pizza}, please. Take your time."],
        "impatient": ["{pizza}. Quick.", "One {pizza}. How long?", "{pizza}, and I'm in a hurry."],
        "chatty": ["Hi! {pizza} please. Lovely weather, right?", "{pizza}! Oh, is that a new oven?", "One {pizza}. So, how's your day going?"],
    },
    "ORDER_COOKING": {
        "patient": ["Smells wonderful already.", "No hurry at all."],
        "impatient": ["Is it done yet?", "How long does an oven TAKE?", "Tick tock."],
        "chatty": ["Ooh, I can hear it sizzling.", "Did you know pizza was invented in Naples?"],
    },
    "ORDER_READY": {
        "patient": ["Thank you kindly!", "Worth the wait.", "Lovely. Thank you."],
        "impatient": ["Finally!", "About time. Thanks.", "Got it. Bye."],
        "chatty": ["Oh this looks AMAZING, thank you!", "Gorgeous! I'll tell everyone!", "Best place in town, I'm telling you."],
    },
    "ORDER_FAILED": {
        "patient": ["Oh. That's a shame.", "Never mind, another time.", "No worries, these things happen."],
        "impatient": ["Unbelievable.", "I waited for THIS?", "Great. Just great."],
        "chatty": ["Oh no! Well, it was nice chatting.", "Aw. The one down the street has pineapple, you know.", "That's okay, I'll tell everyone you tried."],
    },
    "ORDER_UNKNOWN": {
        "patient": ["I'll... wait here, I suppose.", "Take your time figuring it out."],
        "impatient": ["What do you MEAN you don't know?", "Is there a pizza or isn't there?"],
        "chatty": ["Mysterious! Like a pizza whodunit.", "Schrodinger's pizza. Ha. Get it?"],
    },
}


class _Forgiving(dict):
    def __missing__(self, key):
        return "..."


def first_name(items, fallback="ingredients"):
    """The first ingredient of an ``{ingredient: quantity}`` mapping, lower-cased for speech."""
    if isinstance(items, dict) and items:
        return next(iter(items)).lower()
    return fallback


def pick(lines, seed):
    return random.Random(seed).choice(list(lines)) if lines else None


def fill(text, **values):
    words = {"pizza": "pizza", "quantity": 1, "missing": "ingredients", "claimed": "some", "ingredient": "ingredients",
             "name": "you", "short_order": "????"}
    words.update({key: value for key, value in values.items() if value is not None})
    words["pizza"] = str(words["pizza"]).lower()
    return text.format_map(_Forgiving(words))


def chef_line(event_type, payload, mood, seed, **values):
    table = CHEF.get(event_type)
    if table is None:
        return None
    if event_type == "STOCK_CHECKED":
        table = table["sufficient" if payload.get("sufficient") else "short"]
        values["missing"] = first_name(payload.get("missing"))
    elif event_type == "COOK_FAILED":
        table = table.get(payload.get("failureReason"), table["*"])
        values["missing"] = first_name(((payload.get("lastError") or {}).get("details") or {}).get("missing"))
    elif event_type == "RELATIONSHIP_CHANGED":
        missing = payload.get("missing") or {}
        values["missing"] = first_name(missing)
        values["claimed"] = (payload.get("claimed") or {}).get(next(iter(missing), ""), "some")
    text = pick(table.get(mood, table.get("*")), seed)
    return fill(text, **values) if text else None


def stock_worker_line(event_type, payload, seed, **values):
    table = STOCK_WORKER.get(event_type)
    if table is None:
        return None
    if event_type == "STOCK_READ":
        table = table.get(payload.get("source"), table["AUTHORITATIVE"])
    elif event_type == "FAULT_CONFIG_CHANGED":
        faults = payload.get("faults") or {}
        if faults.get("staleStockEnabled"):
            table = table["stale"]
        elif faults.get("failureMode") not in (None, "NONE"):
            table = table["failing"]
        elif faults.get("delayMs"):
            table = table["slow"]
        else:
            table = table["clear"]
    elif event_type == "RESERVATION_REJECTED":
        values["missing"] = first_name(payload.get("items"))
    elif event_type == "STOCK_RESTOCKED":
        values["ingredient"] = first_name(payload.get("added"))
    text = pick(table, seed)
    return fill(text, **values) if text else None


def cashier_line(event_type, payload, trait, seed, **values):
    table = CASHIER.get(event_type)
    if table is None:
        return None
    if event_type == "ORDER_RECEIVED":
        table = table.get(trait, table["patient"])
    elif event_type == "ORDER_FAILED":
        table = table.get(payload.get("failureReason"), table["*"])
        job_error = ((payload.get("lastError") or {}).get("details") or {}).get("jobError") or {}
        values["missing"] = first_name((job_error.get("details") or {}).get("missing"))
    text = pick(table, seed)
    return fill(text, **values) if text else None


def customer_line(event_type, trait, seed, **values):
    table = CUSTOMER.get(event_type)
    if table is None:
        return None
    text = pick(table.get(trait, table["patient"]), seed)
    return fill(text, **values) if text else None
