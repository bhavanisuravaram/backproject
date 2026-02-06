# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
import uuid
import boto3
from boto3.dynamodb.conditions import Key, Attr
from decimal import Decimal
import json

app = Flask(__name__)
app.secret_key = os.urandom(24)

# DynamoDB setup
def get_dynamodb_resource():
    # Using IAM role authentication - no need for explicit credentials
    return boto3.resource('dynamodb', region_name='eu-north-1')

# Get a DynamoDB table
def get_table(table_name):
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(table_name)

# Helper functions
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

def add_transaction(user_id, type, amount, note=None, recipient_id=None):
    transactions_table = get_table('Transactions')
    transaction_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    
    transaction_item = {
        'id': transaction_id,
        'user_id': user_id,
        'type': type,
        'amount': Decimal(str(amount)),
        'timestamp': timestamp,
        'note': note or '',
    }
    
    if recipient_id:
        transaction_item['recipient_id'] = recipient_id
    
    transactions_table.put_item(Item=transaction_item)
    return transaction_id

def update_balance(user_id, amount):
    users_table = get_table('Users')
    # Convert to Decimal for DynamoDB compatibility
    amount_decimal = Decimal(str(amount))
    
    users_table.update_item(
        Key={'id': user_id},
        UpdateExpression='SET balance = balance + :val',
        ExpressionAttributeValues={':val': amount_decimal}
    )

def get_transactions(user_id):
    transactions_table = get_table('Transactions')
    response = transactions_table.scan(
        FilterExpression=Attr('user_id').eq(user_id)
    )
    transactions = response.get('Items', [])
    
    # Sort transactions by timestamp (descending)
    transactions.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Get recipient names for transfer transactions
    users_table = get_table('Users')
    for transaction in transactions:
        if 'recipient_id' in transaction:
            recipient_response = users_table.get_item(Key={'id': transaction['recipient_id']})
            recipient = recipient_response.get('Item')
            if recipient:
                transaction['recipient_name'] = recipient['name']
    
    return transactions

# Routes
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
        
        # Check if email already exists
        if get_user_by_email(email):
            flash('Email already registered!', 'danger')
            return redirect(url_for('register'))
        
        # Create new user
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
        
        # Add initial deposit transaction
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
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    user = get_user(session['user_id'])
    # Convert Decimal to float for template rendering
    if user:
        user['balance'] = float(user['balance'])
    
    return render_template('dashboard.html', user=user, logged_in=True)

@app.route('/deposit', methods=['GET', 'POST'])
def deposit():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        amount = float(request.form['amount'])
        note = request.form.get('note', '')
        
        if amount <= 0:
            flash('Deposit amount must be positive!', 'danger')
            return redirect(url_for('deposit'))
        
        user_id = session['user_id']
        update_balance(user_id, amount)
        add_transaction(user_id, 'deposit', amount, note)
        
        flash('Deposit successful!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('deposit.html', logged_in=True)

@app.route('/withdraw', methods=['GET', 'POST'])
def withdraw():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        amount = float(request.form['amount'])
        note = request.form.get('note', '')
        
        if amount <= 0:
            flash('Withdrawal amount must be positive!', 'danger')
            return redirect(url_for('withdraw'))
        
        user_id = session['user_id']
        user = get_user(user_id)
        
        if float(user['balance']) < amount:
            flash('Insufficient funds!', 'danger')
            return redirect(url_for('withdraw'))
        
        update_balance(user_id, -amount)
        add_transaction(user_id, 'withdraw', amount, note)
        
        flash('Withdrawal successful!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('withdraw.html', logged_in=True)

@app.route('/transfer', methods=['GET', 'POST'])
def transfer():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        recipient_email = request.form['recipient_email']
        amount = float(request.form['amount'])
        mobile_number = request.form.get('mobile_number', '')
        note = request.form.get('note', '')
        
        if amount <= 0:
            flash('Transfer amount must be positive!', 'danger')
            return redirect(url_for('transfer'))
        
        user_id = session['user_id']
        user = get_user(user_id)
        
        if float(user['balance']) < amount:
            flash('Insufficient funds!', 'danger')
            return redirect(url_for('transfer'))
        
        recipient = get_user_by_email(recipient_email)
        if not recipient:
            flash('Recipient not found!', 'danger')
            return redirect(url_for('transfer'))
        
        if recipient['id'] == user_id:
            flash('Cannot transfer to yourself!', 'danger')
            return redirect(url_for('transfer'))
        
        # Process transfer
        update_balance(user_id, -amount)
        update_balance(recipient['id'], amount)
        
        # Create transfer note with mobile number if provided
        transfer_note = note
        if mobile_number:
            transfer_note = f"Mobile: {mobile_number}" + (f" - {note}" if note else "")
        
        # Add transactions for both users
        add_transaction(user_id, 'transfer_sent', amount, transfer_note, recipient['id'])
        add_transaction(recipient['id'], 'transfer_received', amount, f"From {user['name']}: {transfer_note}", user_id)
        
        flash('Transfer successful!', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('transfer.html', logged_in=True)

@app.route('/transactions')
def transactions():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    transactions_list = get_transactions(user_id)
    
    # Convert Decimal to float for template rendering
    for transaction in transactions_list:
        transaction['amount'] = float(transaction['amount'])
    
    return render_template('transactions.html', transactions=transactions_list, logged_in=True)

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user = get_user(user_id)
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'update':
            email = request.form['email']
            current_password = request.form['current_password']
            new_password = request.form.get('new_password', '')
            
            # Verify current password
            if not check_password_hash(user['password_hash'], current_password):
                flash('Current password is incorrect!', 'danger')
                return redirect(url_for('profile'))
            
            # Check if new email already exists and is not the user's current email
            existing_user = get_user_by_email(email)
            if existing_user and existing_user['id'] != user_id:
                flash('Email already in use!', 'danger')
                return redirect(url_for('profile'))
            
            users_table = get_table('Users')
            update_expr = 'SET email = :email'
            expr_values = {
                ':email': email
            }
            
            if new_password:
                password_hash = generate_password_hash(new_password)
                update_expr += ', password_hash = :password_hash'
                expr_values[':password_hash'] = password_hash
                flash('Profile and password updated successfully!', 'success')
            else:
                flash('Profile updated successfully!', 'success')
            
            users_table.update_item(
                Key={'id': user_id},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values
            )
            
        elif action == 'delete':
            password = request.form['delete_password']
            
            # Verify password
            if not check_password_hash(user['password_hash'], password):
                flash('Password is incorrect!', 'danger')
                return redirect(url_for('profile'))
            
            # Delete transactions first
            transactions_table = get_table('Transactions')
            user_transactions = get_transactions(user_id)
            
            for transaction in user_transactions:
                transactions_table.delete_item(Key={'id': transaction['id']})
            
            # Delete user
            users_table = get_table('Users')
            users_table.delete_item(Key={'id': user_id})
            
            session.pop('user_id', None)
            flash('Your account has been deleted.', 'success')
            return redirect(url_for('home'))
        
        return redirect(url_for('profile'))
    
    # Convert Decimal to float for template rendering
    if user:
        user['balance'] = float(user['balance'])
    
    return render_template('profile.html', user=user, logged_in=True)

# Custom JSON encoder to handle Decimal types
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

# Register the custom encoder with Flask
app.json_encoder = DecimalEncoder

if __name__ == '__main__':
    app.run(debug=True)