# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import sqlite3
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Database setup
def get_db_connection():
    conn = sqlite3.connect('banking.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create Users table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        balance REAL NOT NULL
    )
    ''')
    
    # Create Transactions table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount REAL NOT NULL,
        timestamp DATETIME NOT NULL,
        note TEXT,
        recipient_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (recipient_id) REFERENCES users (id)
    )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

# Helper functions
def is_logged_in():
    return 'user_id' in session

def get_user(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

def get_user_by_email(email):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    return user

def add_transaction(user_id, type, amount, note=None, recipient_id=None):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO transactions (user_id, type, amount, timestamp, note, recipient_id) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, type, amount, datetime.now(), note, recipient_id)
    )
    conn.commit()
    conn.close()

def update_balance(user_id, amount):
    conn = get_db_connection()
    conn.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def get_transactions(user_id):
    conn = get_db_connection()
    transactions = conn.execute(
        'SELECT t.*, u.name as recipient_name FROM transactions t LEFT JOIN users u ON t.recipient_id = u.id WHERE t.user_id = ? ORDER BY t.timestamp DESC',
        (user_id,)
    ).fetchall()
    conn.close()
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
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO users (name, email, password_hash, balance) VALUES (?, ?, ?, ?)',
            (name, email, password_hash, initial_deposit)
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
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
        
        if user['balance'] < amount:
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
        
        if user['balance'] < amount:
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
            
            conn = get_db_connection()
            if new_password:
                password_hash = generate_password_hash(new_password)
                conn.execute(
                    'UPDATE users SET email = ?, password_hash = ? WHERE id = ?',
                    (email, password_hash, user_id)
                )
                flash('Profile and password updated successfully!', 'success')
            else:
                conn.execute(
                    'UPDATE users SET email = ? WHERE id = ?',
                    (email, user_id)
                )
                flash('Profile updated successfully!', 'success')
            
            conn.commit()
            conn.close()
            
        elif action == 'delete':
            password = request.form['delete_password']
            
            # Verify password
            if not check_password_hash(user['password_hash'], password):
                flash('Password is incorrect!', 'danger')
                return redirect(url_for('profile'))
            
            # Delete account
            conn = get_db_connection()
            conn.execute('DELETE FROM transactions WHERE user_id = ?', (user_id,))
            conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
            conn.commit()
            conn.close()
            
            session.pop('user_id', None)
            flash('Your account has been deleted.', 'success')
            return redirect(url_for('home'))
        
        return redirect(url_for('profile'))
    
    return render_template('profile.html', user=user, logged_in=True)

if __name__ == '__main__':
    app.run(debug=True)