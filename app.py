from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
import uuid
import boto3
from boto3.dynamodb.conditions import Attr
from decimal import Decimal
import json

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ---------------- DynamoDB Setup ---------------- #

def get_dynamodb_resource():
    return boto3.resource('dynamodb', region_name='eu-north-1')

def get_table(table_name):
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(table_name)

# ---------------- Helper Functions ---------------- #

def is_logged_in():
    return 'user_id' in session

def get_user(user_id):
    users_table = get_table('Users')
    response = users_table.get_item(Key={'id': user_id})
    return response.get('Item')

def get_user_by_email(email):
    users_table = get_table('Users')
    response = users_table.scan(
        FilterExpression=Attr('email').eq(email)
    )
    items = response.get('Items', [])
    return items[0] if items else None

def add_transaction(user_id, tx_type, amount, note=None, recipient_id=None):
    transactions_table = get_table('Transactions')
    transaction_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    item = {
        'id': transaction_id,
        'user_id': user_id,
        'type': tx_type,
        'amount': Decimal(str(amount)),
        'timestamp': timestamp,
        'note': note or ''
    }

    if recipient_id:
        item['recipient_id'] = recipient_id

    transactions_table.put_item(Item=item)

def update_balance(user_id, amount):
    users_table = get_table('Users')
    amount_decimal = Decimal(str(amount))

    users_table.update_item(
        Key={'id': user_id},
        UpdateExpression='SET balance = balance + :val',
        ExpressionAttributeValues={
            ':val': amount_decimal
        }
    )

def get_transactions(user_id):
    transactions_table = get_table('Transactions')
    users_table = get_table('Users')

    response = transactions_table.scan(
        FilterExpression=Attr('user_id').eq(user_id)
    )

    transactions = response.get('Items', [])
    transactions.sort(key=lambda x: x['timestamp'], reverse=True)

    for transaction in transactions:
        if 'recipient_id' in transaction:
            recipient = users_table.get_item(
                Key={'id': transaction['recipient_id']}
            ).get('Item')
            if recipient:
                transaction['recipient_name'] = recipient['name']

        transaction['amount'] = float(transaction['amount'])

    return transactions

# ---------------- Routes ---------------- #

@app.route('/')
def home():
    return render_template('home.html', logged_in=is_logged_in())

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        initial_deposit = float(request.form['initial_deposit'])

        if initial_deposit < 0:
            flash('Initial deposit cannot be negative!', 'danger')
            return redirect(url_for('register'))

        if get_user_by_email(email):
            flash('Email already registered!', 'danger')
            return redirect(url_for('register'))

        password_hash = generate_password_hash(password)
        user_id = str(uuid.uuid4())

        users_table = get_table('Users')
        users_table.put_item(
            Item={
                'id': user_id,
                'name': name,
                'email': email,
                'password_hash': password_hash,
                'balance': Decimal(str(initial_deposit))
            }
        )

        if initial_deposit > 0:
            add_transaction(user_id, 'deposit', initial_deposit, 'Initial deposit')

        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', logged_in=is_logged_in())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = get_user_by_email(email)

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            flash('Logged in successfully!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password!', 'danger')

    return render_template('login.html', logged_in=is_logged_in())

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if not is_logged_in():
        return redirect(url_for('login'))

    user = get_user(session['user_id'])
    if user:
        user['balance'] = float(user['balance'])

    return render_template('dashboard.html', user=user, logged_in=True)

@app.route('/deposit', methods=['GET', 'POST'])
def deposit():
    if not is_logged_in():
        return redirect(url_for('login'))

    if request.method == 'POST':
        amount = float(request.form['amount'])
        note = request.form.get('note', '')

        if amount <= 0:
            flash('Deposit must be positive!', 'danger')
            return redirect(url_for('deposit'))

        update_balance(session['user_id'], amount)
        add_transaction(session['user_id'], 'deposit', amount, note)

        flash('Deposit successful!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('deposit.html', logged_in=True)

@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    if not is_logged_in():
        return redirect(url_for('login'))

    if request.method == 'POST':
        amount = float(request.form['amount'])
        note = request.form.get('note', '')

        user = get_user(session['user_id'])

        if float(user['balance']) < amount:
            flash('Insufficient funds!', 'danger')
            return redirect(url_for('withdraw'))

        update_balance(session['user_id'], -amount)
        add_transaction(session['user_id'], 'withdraw', amount, note)

        flash('Withdrawal successful!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('withdraw.html', logged_in=True)

@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    if not is_logged_in():
        return redirect(url_for('login'))

    if request.method == 'POST':
        recipient_email = request.form['recipient_email']
        amount = float(request.form['amount'])
        note = request.form.get('note', '')

        sender = get_user(session['user_id'])

        if float(sender['balance']) < amount:
            flash('Insufficient funds!', 'danger')
            return redirect(url_for('transfer'))

        recipient = get_user_by_email(recipient_email)

        if not recipient:
            flash('Recipient not found!', 'danger')
            return redirect(url_for('transfer'))

        update_balance(sender['id'], -amount)
        update_balance(recipient['id'], amount)

        add_transaction(sender['id'], 'transfer_sent', amount, note, recipient['id'])
        add_transaction(recipient['id'], 'transfer_received', amount, note, sender['id'])

        flash('Transfer successful!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('transfer.html', logged_in=True)

@app.route('/transactions')
def transactions():
    if not is_logged_in():
        return redirect(url_for('login'))

    transactions = get_transactions(session['user_id'])
    return render_template('transactions.html', transactions=transactions, logged_in=True)

# ---------------- Run App ---------------- #

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
