# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
import boto3
from boto3.dynamodb.conditions import Key, Attr
import uuid
from decimal import Decimal

app = Flask(__name__)
# Use a persistent secret key - consider loading from environment variable in production
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))

# DynamoDB setup
def get_dynamodb_resource():
    # Use IAM role attached to EC2 instance
    # The EC2 instance will automatically use the attached role
    return boto3.resource('dynamodb', region_name='eu-north-1')

def init_db():
    dynamodb = get_dynamodb_resource()
    
    # Check if Users table exists, if not create it
    existing_tables = [table.name for table in dynamodb.tables.all()]
    
    if 'Users' not in existing_tables:
        users_table = dynamodb.create_table(
            TableName='Users',
            KeySchema=[
                {
                    'AttributeName': 'id',
                    'KeyType': 'HASH'  # Partition key
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'email',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'EmailIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'email',
                            'KeyType': 'HASH'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        
        # Wait for the table to be created
        users_table.meta.client.get_waiter('table_exists').wait(TableName='Users')
    
    if 'Transactions' not in existing_tables:
        transactions_table = dynamodb.create_table(
            TableName='Transactions',
            KeySchema=[
                {
                    'AttributeName': 'id',
                    'KeyType': 'HASH'  # Partition key
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'id',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'user_id',
                    'AttributeType': 'S'
                }
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': 'UserIdIndex',
                    'KeySchema': [
                        {
                            'AttributeName': 'user_id',
                            'KeyType': 'HASH'
                        }
                    ],
                    'Projection': {
                        'ProjectionType': 'ALL'
                    },
                    'ProvisionedThroughput': {
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )
        
        # Wait for the table to be created
        transactions_table.meta.client.get_waiter('table_exists').wait(TableName='Transactions')

# Initialize database
init_db()

# Helper functions
def is_logged_in():
    return 'user_id' in session

def get_user(user_id):
    dynamodb = get_dynamodb_resource()
    users_table = dynamodb.Table('Users')
    
    response = users_table.get_item(
        Key={
            'id': user_id
        }
    )
    
    if 'Item' in response:
        return response['Item']
    return None

def get_user_by_email(email):
    dynamodb = get_dynamodb_resource()
    users_table = dynamodb.Table('Users')
    
    response = users_table.query(
        IndexName='EmailIndex',
        KeyConditionExpression=Key('email').eq(email)
    )
    
    if response['Items'] and len(response['Items']) > 0:
        return response['Items'][0]
    return None

def add_transaction(user_id, type, amount, note=None, recipient_id=None):
    dynamodb = get_dynamodb_resource()
    transactions_table = dynamodb.Table('Transactions')
    
    transaction_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    
    transaction_item = {
        'id': transaction_id,
        'user_id': user_id,
        'type': type,
        'amount': Decimal(str(amount)),  # Convert float to Decimal for DynamoDB
        'timestamp': timestamp,
    }
    
    if note:
        transaction_item['note'] = note
        
    if recipient_id:
        transaction_item['recipient_id'] = recipient_id
    
    transactions_table.put_item(
        Item=transaction_item
    )

def update_balance(user_id, amount):
    dynamodb = get_dynamodb_resource()
    users_table = dynamodb.Table('Users')
    
    # DynamoDB doesn't support direct increment/decrement like SQL
    # We need to get the current balance first
    user = get_user(user_id)
    new_balance = Decimal(str(user['balance'])) + Decimal(str(amount))
    
    users_table.update_item(
        Key={
            'id': user_id
        },
        UpdateExpression='SET balance = :balance',
        ExpressionAttributeValues={
            ':balance': new_balance
        }
    )

def get_transactions(user_id):
    dynamodb = get_dynamodb_resource()
    transactions_table = dynamodb.Table('Transactions')
    users_table = dynamodb.Table('Users')
    
    # Query transactions for the user
    response = transactions_table.query(
        IndexName='UserIdIndex',
        KeyConditionExpression=Key('user_id').eq(user_id)
    )
    
    transactions = sorted(response['Items'], key=lambda x: x['timestamp'], reverse=True)
    
    # Add recipient names to transactions
    for transaction in transactions:
        if 'recipient_id' in transaction:
            recipient = get_user(transaction['recipient_id'])
            if recipient:
                transaction['recipient_name'] = recipient['name']
        
        # Convert Decimal to float for template rendering
        transaction['amount'] = float(transaction['amount'])
    
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
        
        dynamodb = get_dynamodb_resource()
        users_table = dynamodb.Table('Users')
        
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
        
        if Decimal(str(user['balance'])) < Decimal(str(amount)):
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
        
        if Decimal(str(user['balance'])) < Decimal(str(amount)):
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
    transactions = get_transactions(user_id)
    
    return render_template('transactions.html', transactions=transactions, logged_in=True)

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        flash('Please log in first!', 'danger')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user = get_user(user_id)
    # Convert Decimal to float for template rendering
    user['balance'] = float(user['balance'])
    
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
            
            # Check if new email already exists
            if email != user['email'] and get_user_by_email(email):
                flash('Email already in use!', 'danger')
                return redirect(url_for('profile'))
            
            dynamodb = get_dynamodb_resource()
            users_table = dynamodb.Table('Users')
            
            update_expression = 'SET email = :email'
            expression_attribute_values = {
                ':email': email
            }
            
            if new_password:
                password_hash = generate_password_hash(new_password)
                update_expression += ', password_hash = :password_hash'
                expression_attribute_values[':password_hash'] = password_hash
                flash('Profile and password updated successfully!', 'success')
            else:
                flash('Profile updated successfully!', 'success')
            
            users_table.update_item(
                Key={
                    'id': user_id
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_attribute_values
            )
            
        elif action == 'delete':
            password = request.form['delete_password']
            
            # Verify password
            if not check_password_hash(user['password_hash'], password):
                flash('Password is incorrect!', 'danger')
                return redirect(url_for('profile'))
            
            # Delete account and transactions
            dynamodb = get_dynamodb_resource()
            users_table = dynamodb.Table('Users')
            transactions_table = dynamodb.Table('Transactions')
            
            # Get all user transactions
            response = transactions_table.query(
                IndexName='UserIdIndex',
                KeyConditionExpression=Key('user_id').eq(user_id)
            )
            
            # Delete each transaction
            with transactions_table.batch_writer() as batch:
                for transaction in response['Items']:
                    batch.delete_item(
                        Key={
                            'id': transaction['id']
                        }
                    )
            
            # Delete user
            users_table.delete_item(
                Key={
                    'id': user_id
                }
            )
            
            session.pop('user_id', None)
            flash('Your account has been deleted.', 'success')
            return redirect(url_for('home'))
        
        return redirect(url_for('profile'))
    
    return render_template('profile.html', user=user, logged_in=True)

if __name__ == '__main__':
    # For production, set debug to False
    app.run(host='0.0.0.0', port=80, debug=True)