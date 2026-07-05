import os
import sqlite3
import secrets
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "lucky38.db"
START_BALANCE = 1000
CASINO_START_TREASURY = 50000

SLOT_SYMBOLS = [
    {"code": "cherry", "label": "Вишня", "icon": "🍒"},
    {"code": "bell", "label": "Колокол", "icon": "🔔"},
    {"code": "cap", "label": "Крышка", "icon": "🥤"},
    {"code": "seven", "label": "Семёрка", "icon": "7"},
    {"code": "radiation", "label": "Радиация", "icon": "☢"},
]
ALLOWED_SLOT_BETS = (10, 25, 50, 100, 250)

ROULETTE_RED_NUMBERS = {
    1, 3, 5, 7, 9, 12, 14, 16, 18,
    19, 21, 23, 25, 27, 30, 32, 34, 36,
}
ALLOWED_ROULETTE_BETS = (10, 25, 50, 100, 250)


def roulette_color(number):
    if number == 0:
        return "green"
    return "red" if number in ROULETTE_RED_NUMBERS else "black"


def evaluate_roulette_bet(number, bet_type, bet_value):
    color = roulette_color(number)

    if bet_type == "number":
        selected_number = int(bet_value)
        return number == selected_number, 36, f"Число {selected_number}"
    if bet_type == "color":
        labels = {"red": "Красное", "black": "Чёрное"}
        return color == bet_value, 2, labels[bet_value]
    if bet_type == "parity":
        labels = {"even": "Чётное", "odd": "Нечётное"}
        won = number != 0 and ((number % 2 == 0) == (bet_value == "even"))
        return won, 2, labels[bet_value]
    if bet_type == "range":
        labels = {"low": "1–18", "high": "19–36"}
        won = (bet_value == "low" and 1 <= number <= 18) or (
            bet_value == "high" and 19 <= number <= 36
        )
        return won, 2, labels[bet_value]
    if bet_type == "dozen":
        labels = {"first": "1-я дюжина", "second": "2-я дюжина", "third": "3-я дюжина"}
        won = (
            (bet_value == "first" and 1 <= number <= 12)
            or (bet_value == "second" and 13 <= number <= 24)
            or (bet_value == "third" and 25 <= number <= 36)
        )
        return won, 3, labels[bet_value]

    raise ValueError("Недопустимый тип ставки")

ALLOWED_BLACKJACK_BETS = (10, 25, 50, 100, 250)
BLACKJACK_SUITS = ("♠", "♥", "♦", "♣")
BLACKJACK_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")


def create_blackjack_deck():
    deck = [f"{rank}|{suit}" for suit in BLACKJACK_SUITS for rank in BLACKJACK_RANKS]
    secrets.SystemRandom().shuffle(deck)
    return deck


def card_parts(card):
    rank, suit = card.split("|", 1)
    return {"rank": rank, "suit": suit, "red": suit in ("♥", "♦")}


def hand_value(hand):
    value = 0
    aces = 0
    for card in hand:
        rank = card.split("|", 1)[0]
        if rank == "A":
            value += 11
            aces += 1
        elif rank in ("J", "Q", "K"):
            value += 10
        else:
            value += int(rank)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def is_blackjack(hand):
    return len(hand) == 2 and hand_value(hand) == 21

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lucky38-dev-secret-key")
app.config["DATABASE"] = DATABASE


def get_db():
    if "db" not in g:
        DATABASE.parent.mkdir(exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 1000,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS casino (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            treasury INTEGER NOT NULL,
            initial_treasury INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS game_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_type TEXT NOT NULL,
            bet INTEGER NOT NULL,
            result TEXT NOT NULL,
            payout INTEGER NOT NULL DEFAULT 0,
            balance_after INTEGER NOT NULL,
            casino_treasury_after INTEGER NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
        """
    )
    db.execute(
        """
        INSERT OR IGNORE INTO casino (id, treasury, initial_treasury)
        VALUES (1, ?, ?)
        """,
        (CASINO_START_TREASURY, CASINO_START_TREASURY),
    )
    db.commit()



def evaluate_slots(result):
    codes = [symbol["code"] for symbol in result]

    if len(set(codes)) == 1:
        if codes[0] == "radiation":
            return 10, "Джекпот: три знака радиации"
        if codes[0] == "seven":
            return 7, "Три семёрки"
        if codes[0] == "cap":
            return 5, "Три крышки"
        return 4, "Три одинаковых символа"

    if codes.count("cherry") == 2:
        return 2, "Две вишни"

    return 0, "Выигрышной комбинации нет"


def draw_slot_symbol():
    # Более простые символы выпадают чаще, а крупные выигрыши — реже.
    weighted_codes = (
        "cherry", "cherry", "cherry", "cherry",
        "bell", "bell", "bell",
        "cap", "cap",
        "seven",
        "radiation",
    )
    code = secrets.choice(weighted_codes)
    return next(symbol for symbol in SLOT_SYMBOLS if symbol["code"] == code)

def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash("Сначала войдите в аккаунт.", "error")
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
    else:
        g.user = get_db().execute(
            "SELECT id, username, balance, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()


@app.context_processor
def inject_casino_data():
    casino = get_db().execute(
        "SELECT treasury, initial_treasury FROM casino WHERE id = 1"
    ).fetchone()
    return {"casino": casino}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=("GET", "POST"))
def register():
    if g.user is not None:
        return redirect(url_for("profile"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        error = None

        if len(username) < 3:
            error = "Логин должен содержать не менее 3 символов."
        elif len(username) > 30:
            error = "Логин не должен быть длиннее 30 символов."
        elif not username.replace("_", "").isalnum():
            error = "В логине разрешены только буквы, цифры и знак подчёркивания."
        elif len(password) < 6:
            error = "Пароль должен содержать не менее 6 символов."
        elif password != password_confirm:
            error = "Пароли не совпадают."

        if error is None:
            db = get_db()
            try:
                cursor = db.execute(
                    """
                    INSERT INTO users (username, password_hash, balance)
                    VALUES (?, ?, ?)
                    """,
                    (username, generate_password_hash(password), START_BALANCE),
                )
                db.commit()
            except sqlite3.IntegrityError:
                error = "Пользователь с таким логином уже существует."
            else:
                session.clear()
                session["user_id"] = cursor.lastrowid
                flash(
                    f"Добро пожаловать в Lucky 38! Вам начислено {START_BALANCE} крышек.",
                    "success",
                )
                return redirect(url_for("profile"))

        flash(error, "error")

    return render_template("register.html")


@app.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("profile"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Неверный логин или пароль.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("Вы успешно вошли в аккаунт.", "success")
            return redirect(url_for("profile"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "success")
    return redirect(url_for("index"))



@app.route("/slots", methods=("GET", "POST"))
@login_required
def slots():
    result = None
    spin_message = None
    multiplier = 0
    payout = 0
    selected_bet = 25

    if request.method == "POST":
        try:
            selected_bet = int(request.form.get("bet", 0))
        except (TypeError, ValueError):
            selected_bet = 0

        db = get_db()
        user = db.execute(
            "SELECT id, balance FROM users WHERE id = ?", (g.user["id"],)
        ).fetchone()
        casino_row = db.execute(
            "SELECT treasury FROM casino WHERE id = 1"
        ).fetchone()

        if selected_bet not in ALLOWED_SLOT_BETS:
            flash("Выберите допустимую ставку.", "error")
        elif casino_row["treasury"] <= 0:
            flash("Казна Lucky 38 пуста. Казино разорено!", "error")
        elif user["balance"] < selected_bet:
            flash("Недостаточно крышек для такой ставки.", "error")
        else:
            result = [draw_slot_symbol() for _ in range(3)]
            multiplier, spin_message = evaluate_slots(result)
            theoretical_payout = selected_bet * multiplier

            # Сначала ставка поступает в казну, затем из неё выплачивается выигрыш.
            treasury_after_bet = casino_row["treasury"] + selected_bet
            payout = min(theoretical_payout, treasury_after_bet)
            new_balance = user["balance"] - selected_bet + payout
            new_treasury = treasury_after_bet - payout

            if theoretical_payout > payout:
                spin_message += ". Казино выплатило всю оставшуюся казну"

            db.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, user["id"]),
            )
            db.execute(
                "UPDATE casino SET treasury = ? WHERE id = 1",
                (new_treasury,),
            )
            db.execute(
                """
                INSERT INTO game_history
                    (user_id, game_type, bet, result, payout,
                     balance_after, casino_treasury_after)
                VALUES (?, 'slots', ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    selected_bet,
                    " | ".join(symbol["code"] for symbol in result),
                    payout,
                    new_balance,
                    new_treasury,
                ),
            )
            db.commit()

            # Обновляем g.user, чтобы новый баланс сразу появился в шапке.
            g.user = db.execute(
                "SELECT id, username, balance, created_at FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()

    recent_games = get_db().execute(
        """
        SELECT bet, result, payout, balance_after, created_at
        FROM game_history
        WHERE user_id = ? AND game_type = 'slots'
        ORDER BY id DESC
        LIMIT 5
        """,
        (g.user["id"],),
    ).fetchall()

    return render_template(
        "slots.html",
        symbols=SLOT_SYMBOLS,
        bets=ALLOWED_SLOT_BETS,
        selected_bet=selected_bet,
        result=result,
        spin_message=spin_message,
        multiplier=multiplier,
        payout=payout,
        recent_games=recent_games,
    )


@app.route("/roulette", methods=("GET", "POST"))
@login_required
def roulette():
    result_number = None
    result_color = None
    result_message = None
    payout = 0
    multiplier = 0
    selected_bet = 25
    selected_type = "color"
    selected_value = "red"

    if request.method == "POST":
        try:
            selected_bet = int(request.form.get("bet", 0))
        except (TypeError, ValueError):
            selected_bet = 0

        selected_type = request.form.get("bet_type", "")
        selected_value = request.form.get("bet_value", "")

        valid_values = {
            "number": {str(number) for number in range(37)},
            "color": {"red", "black"},
            "parity": {"even", "odd"},
            "range": {"low", "high"},
            "dozen": {"first", "second", "third"},
        }

        db = get_db()
        user = db.execute(
            "SELECT id, balance FROM users WHERE id = ?", (g.user["id"],)
        ).fetchone()
        casino_row = db.execute(
            "SELECT treasury FROM casino WHERE id = 1"
        ).fetchone()

        if selected_bet not in ALLOWED_ROULETTE_BETS:
            flash("Выберите допустимую ставку.", "error")
        elif selected_type not in valid_values or selected_value not in valid_values[selected_type]:
            flash("Выберите корректный вариант ставки.", "error")
        elif casino_row["treasury"] <= 0:
            flash("Казна Lucky 38 пуста. Казино разорено!", "error")
        elif user["balance"] < selected_bet:
            flash("Недостаточно крышек для такой ставки.", "error")
        else:
            result_number = secrets.randbelow(37)
            result_color = roulette_color(result_number)
            won, multiplier, bet_label = evaluate_roulette_bet(
                result_number, selected_type, selected_value
            )
            theoretical_payout = selected_bet * multiplier if won else 0

            treasury_after_bet = casino_row["treasury"] + selected_bet
            payout = min(theoretical_payout, treasury_after_bet)
            new_balance = user["balance"] - selected_bet + payout
            new_treasury = treasury_after_bet - payout

            result_message = (
                f"Ставка «{bet_label}» сыграла"
                if won
                else f"Ставка «{bet_label}» не сыграла"
            )
            if theoretical_payout > payout:
                result_message += ". Казино выплатило всю оставшуюся казну"

            db.execute(
                "UPDATE users SET balance = ? WHERE id = ?",
                (new_balance, user["id"]),
            )
            db.execute(
                "UPDATE casino SET treasury = ? WHERE id = 1",
                (new_treasury,),
            )
            db.execute(
                """
                INSERT INTO game_history
                    (user_id, game_type, bet, result, payout,
                     balance_after, casino_treasury_after)
                VALUES (?, 'roulette', ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    selected_bet,
                    f"{result_number}:{result_color}:{selected_type}:{selected_value}",
                    payout,
                    new_balance,
                    new_treasury,
                ),
            )
            db.commit()

            g.user = db.execute(
                "SELECT id, username, balance, created_at FROM users WHERE id = ?",
                (user["id"],),
            ).fetchone()

    recent_games = get_db().execute(
        """
        SELECT bet, result, payout, balance_after, created_at
        FROM game_history
        WHERE user_id = ? AND game_type = 'roulette'
        ORDER BY id DESC
        LIMIT 5
        """,
        (g.user["id"],),
    ).fetchall()

    parsed_games = []
    for game in recent_games:
        number, color, _bet_type, _bet_value = game["result"].split(":", 3)
        parsed_games.append({
            "bet": game["bet"],
            "number": number,
            "color": color,
            "payout": game["payout"],
        })

    return render_template(
        "roulette.html",
        bets=ALLOWED_ROULETTE_BETS,
        selected_bet=selected_bet,
        selected_type=selected_type,
        selected_value=selected_value,
        result_number=result_number,
        result_color=result_color,
        result_message=result_message,
        payout=payout,
        multiplier=multiplier,
        recent_games=parsed_games,
    )

def finish_blackjack_game(game, result_code, message, payout):
    db = get_db()
    user = db.execute("SELECT id, balance FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    casino_row = db.execute("SELECT treasury FROM casino WHERE id = 1").fetchone()
    actual_payout = min(payout, casino_row["treasury"])
    new_balance = user["balance"] + actual_payout
    new_treasury = casino_row["treasury"] - actual_payout
    if payout > actual_payout:
        message += ". Казино выплатило всю оставшуюся казну"

    db.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user["id"]))
    db.execute("UPDATE casino SET treasury = ? WHERE id = 1", (new_treasury,))
    db.execute(
        """
        INSERT INTO game_history
            (user_id, game_type, bet, result, payout, balance_after, casino_treasury_after)
        VALUES (?, 'blackjack', ?, ?, ?, ?, ?)
        """,
        (user["id"], game["bet"], result_code, actual_payout, new_balance, new_treasury),
    )
    db.commit()
    session.pop("blackjack_game", None)
    session["blackjack_last"] = {
        "player": game["player"],
        "dealer": game["dealer"],
        "message": message,
        "payout": actual_payout,
        "bet": game["bet"],
        "result": result_code,
    }
    g.user = db.execute(
        "SELECT id, username, balance, created_at FROM users WHERE id = ?",
        (user["id"],),
    ).fetchone()


def dealer_play(game):
    while hand_value(game["dealer"]) < 17:
        game["dealer"].append(game["deck"].pop())


def resolve_blackjack(game):
    player_value = hand_value(game["player"])
    dealer_value = hand_value(game["dealer"])
    bet = game["bet"]

    if player_value > 21:
        finish_blackjack_game(game, "loss", "Перебор. Дилер побеждает", 0)
    elif dealer_value > 21:
        finish_blackjack_game(game, "win", "У дилера перебор. Вы победили", bet * 2)
    elif player_value > dealer_value:
        finish_blackjack_game(game, "win", "Вы набрали больше очков и победили", bet * 2)
    elif player_value < dealer_value:
        finish_blackjack_game(game, "loss", "Дилер набрал больше очков", 0)
    else:
        finish_blackjack_game(game, "push", "Ничья. Ставка возвращена", bet)


@app.route("/blackjack", methods=("GET", "POST"))
@login_required
def blackjack():
    game = session.get("blackjack_game")
    last_game = session.pop("blackjack_last", None)
    selected_bet = 25

    if request.method == "POST":
        action = request.form.get("action", "")
        db = get_db()
        user = db.execute("SELECT id, balance FROM users WHERE id = ?", (g.user["id"],)).fetchone()
        casino_row = db.execute("SELECT treasury FROM casino WHERE id = 1").fetchone()

        if action == "start":
            try:
                selected_bet = int(request.form.get("bet", 0))
            except (TypeError, ValueError):
                selected_bet = 0

            if game:
                flash("Сначала завершите текущую партию.", "error")
            elif selected_bet not in ALLOWED_BLACKJACK_BETS:
                flash("Выберите допустимую ставку.", "error")
            elif casino_row["treasury"] <= 0:
                flash("Казна Lucky 38 пуста. Казино разорено!", "error")
            elif user["balance"] < selected_bet:
                flash("Недостаточно крышек для такой ставки.", "error")
            else:
                deck = create_blackjack_deck()
                game = {
                    "deck": deck,
                    "player": [deck.pop(), deck.pop()],
                    "dealer": [deck.pop(), deck.pop()],
                    "bet": selected_bet,
                    "doubled": False,
                }
                db.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (selected_bet, user["id"]))
                db.execute("UPDATE casino SET treasury = treasury + ? WHERE id = 1", (selected_bet,))
                db.commit()
                session["blackjack_game"] = game

                player_bj = is_blackjack(game["player"])
                dealer_bj = is_blackjack(game["dealer"])
                if player_bj and dealer_bj:
                    finish_blackjack_game(game, "push", "У игрока и дилера блэкджек. Ничья", selected_bet)
                    game = None
                elif player_bj:
                    finish_blackjack_game(game, "blackjack", "Блэкджек! Выплата 3 к 2", selected_bet * 5 // 2)
                    game = None
                elif dealer_bj:
                    finish_blackjack_game(game, "loss", "У дилера блэкджек", 0)
                    game = None
                else:
                    g.user = db.execute(
                        "SELECT id, username, balance, created_at FROM users WHERE id = ?",
                        (user["id"],),
                    ).fetchone()

        elif not game:
            flash("Сначала начните новую партию.", "error")
        elif action == "hit":
            game["player"].append(game["deck"].pop())
            session["blackjack_game"] = game
            session.modified = True
            if hand_value(game["player"]) > 21:
                resolve_blackjack(game)
                game = None
        elif action == "stand":
            dealer_play(game)
            resolve_blackjack(game)
            game = None
        elif action == "double":
            if len(game["player"]) != 2:
                flash("Удвоить ставку можно только сразу после раздачи.", "error")
            elif user["balance"] < game["bet"]:
                flash("Недостаточно крышек для удвоения.", "error")
            else:
                extra_bet = game["bet"]
                game["bet"] *= 2
                game["doubled"] = True
                db.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (extra_bet, user["id"]))
                db.execute("UPDATE casino SET treasury = treasury + ? WHERE id = 1", (extra_bet,))
                db.commit()
                game["player"].append(game["deck"].pop())
                if hand_value(game["player"]) <= 21:
                    dealer_play(game)
                resolve_blackjack(game)
                game = None
        else:
            flash("Неизвестное действие.", "error")

        if session.get("blackjack_last"):
            last_game = session.pop("blackjack_last")

    game = session.get("blackjack_game")
    recent_games = get_db().execute(
        """
        SELECT bet, result, payout, created_at
        FROM game_history
        WHERE user_id = ? AND game_type = 'blackjack'
        ORDER BY id DESC
        LIMIT 5
        """,
        (g.user["id"],),
    ).fetchall()

    player_cards = [card_parts(card) for card in game["player"]] if game else []
    dealer_cards = [card_parts(card) for card in game["dealer"]] if game else []
    if last_game:
        last_game = dict(last_game)
        last_game["player_cards"] = [card_parts(card) for card in last_game["player"]]
        last_game["dealer_cards"] = [card_parts(card) for card in last_game["dealer"]]
        last_game["player_value"] = hand_value(last_game["player"])
        last_game["dealer_value"] = hand_value(last_game["dealer"])

    return render_template(
        "blackjack.html",
        bets=ALLOWED_BLACKJACK_BETS,
        selected_bet=selected_bet,
        game=game,
        player_cards=player_cards,
        dealer_cards=dealer_cards,
        player_value=hand_value(game["player"]) if game else None,
        dealer_visible_value=hand_value(game["dealer"][1:]) if game else None,
        last_game=last_game,
        recent_games=recent_games,
    )


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True)
