# Q
Q is a lightweight monitoring and reporting system.

The name Q originates from the fictional character as well as its species in the series [Star Trek](https://en.wikipedia.org/wiki/Q_(Star_Trek)).

## Structure

```
                     +-------------+
                     |      Q      |
                     |     Web     |
                     +-------------+
                    /               \
                   /                 \
        +-----------+                +-----------+
        |     Q     |                |     Q     |
        |   Proxy   |                |   Proxy   |
        +-----------+                +-----------+
        /           \                      |
       /             \                     |
+-----------+   +-----------+        +-----------+
| Machine 1 |   | Machine 2 |        | Machine 3 |
+-----------+   +-----------+        +-----------+
```

## Setting up a development environment

### q_web
Begin using `q_web/` as cwd:

```bash
python -m venv venv
python -m pip install -r requirements.txt
npm init -y
npm install babel-cli@6 babel-preset-react-app@3
```

To start the local dev server:
`python start_q_web_dev.py`

To start the JSX transpiler:
`bash start_jsx_transpiler.sh`


## API
Use the endpoint as salt. The key for the checksum is "checksum".
  
- POST `/api/v1/authenticate`
    - Parameters: `username`, `password`
    - Returns: `token`
    
You can use the retrieved token in the header `HTTP_AUTHENTICATION`. 
To do so, generate a string in the form `"<username>:<token>"` use urlsafe b64encoding with UTF-8 encoding. 
The resulting value should be put as the header value.  

### Metrics
- GET `/api/v1/metrics`

Use this to retrieve all metrics 

- GET `/api/v1/metrics/{id}`

Get a specific metric

- POST `/api/v1/metrics`

Create a new metric

- PUT `/api/v1/metrics/{id}`

Update a metric

- DELETE `/api/v1/metrics/{id}`

Delete a metric
