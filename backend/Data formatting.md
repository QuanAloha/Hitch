- Location node
	- Individual stop location
- Delivery Tickets
	- Individual pickup/delivery batches
- Routes
	- A collection of location nodes
	- For map routing/driving
- Dispatches
	- A route
	- With a list of delivery tickets
		- Use the stop_id to reference a location_node
	- Get's passed to driver


- Available Drivers
	- Driver ID
	- Route Origin
	- Route End
	- Shift Start
	- Shift End

- Cars
	- Make
	- Model
	- Year
	- Color
	- MPG
	- Cargo Capacity

- Drivers
	- DriverID
	- Name
	- Location
	- Car ID