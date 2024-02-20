# Keep running following command until user decides to stop
while true; do
	python3 gsync.py
	grep -Hn "^" */*.md
	# Wait for user to decide to stop
	read -p "Press [Enter] to continue or [Ctrl+C] to stop..."
	if [ $? -ne 0 ]; then
		break
	fi
done

