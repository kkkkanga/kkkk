import psycopg2, traceback

def try_connect(host):
    print(f'Trying host={host}')
    try:
        psycopg2.connect(dbname='camfitdb', user='camfit_user', password='mr001125!', host=host, port=5432)
        print('connected OK')
    except Exception as e:
        print('EXC:', type(e), e)
        traceback.print_exc()

if __name__ == '__main__':
    try_connect('db')
    try_connect('localhost')
    try_connect('127.0.0.1')
