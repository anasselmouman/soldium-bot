"""اختبارات ترقية مستوى الإحالة بعد دفع عمولة."""



from __future__ import annotations



from pathlib import Path



import database as db





def _use_temp_db(tmp_path: Path) -> None:

    db.DB_PATH = tmp_path / "test_referral_level.db"

    db.init_db()





def _grant_balance(uid: int, amount: float) -> None:

    dep_id = db.create_deposit(uid, 0.0, "CashPlus", f"proof_grant_{uid}_{amount}")

    assert dep_id is not None

    assert db.finalize_approved_deposit(dep_id, uid, amount, "CashPlus") is True





def test_payout_upgrades_referrer_to_level_2(tmp_path: Path) -> None:

    _use_temp_db(tmp_path)

    referrer_id = 1000

    db.add_user(referrer_id)

    with db.get_connection() as connection:

        connection.execute(

            "UPDATE users SET referral_earned_total = 150, referral_level = 1 WHERE user_id = ?",

            (referrer_id,),

        )

        connection.commit()



    for i in range(10):

        invitee = 2000 + i

        db.add_user(invitee)

        with db.get_connection() as connection:

            connection.execute(

                "UPDATE users SET referred_by = ? WHERE user_id = ?",

                (referrer_id, invitee),

            )

            connection.commit()

        _grant_balance(invitee, 20.0)

        oid = db.create_order_with_balance_hold(invitee, "svc", str(i), "http://x", 1, 10.0)

        assert oid is not None

        with db.get_connection() as connection:

            connection.execute(

                "UPDATE orders SET status = 'completed', referral_payout_done = 1 WHERE id = ?",

                (oid,),

            )

            connection.commit()



    invitee_last = 2010

    db.add_user(invitee_last)

    with db.get_connection() as connection:

        connection.execute(

            "UPDATE users SET referred_by = ? WHERE user_id = ?",

            (referrer_id, invitee_last),

        )

        connection.commit()

    _grant_balance(invitee_last, 100.0)

    oid_last = db.create_order_with_balance_hold(invitee_last, "svc", "last", "http://y", 1, 100.0)
    assert oid_last is not None
    with db.get_connection() as connection:
        connection.execute(
            "UPDATE orders SET status = 'completed' WHERE id = ?",
            (oid_last,),
        )
        connection.commit()
    db.try_apply_referral_payout_for_order(oid_last)



    ref = db.get_user(referrer_id)

    assert ref is not None

    assert int(ref["referral_level"]) == 2

    assert db.count_active_referred_users(referrer_id) >= 10

